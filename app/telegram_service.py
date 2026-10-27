import asyncio
import html
import logging
import re
import time
from typing import Awaitable, Callable

import requests

from app.cities import CityStore
from app.config import AppConfig
from app.history import HistoryStore
from app.telegram_store import TelegramStore
from app.call_store import CallStore
from app.employees import EmployeeStore
from app.telegram_flows import TelegramFlows
from app.telegram_interactions import TelegramInteractions
from app.dates import display_date
from app.telegram_profile import TelegramProfile
from app.rate_limit import InteractionLimiter
from app.redaction import redact_sensitive
from app.telegram_private import TelegramPrivate


class TelegramError(Exception):
    def __init__(self, message: str, code: int = 0, retryable: bool = False,
                 uncertain: bool = False, retry_after: float = 0) -> None:
        super().__init__(redact_sensitive(message))
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain
        self.retry_after = retry_after


class TelegramAPI:
    async def call(self, token: str, method: str, **payload):
        return await asyncio.to_thread(self._call_sync, token, method, payload)

    @staticmethod
    def _call_sync(token: str, method: str, payload: dict):
        try:
            timeout = (3, 5) if method == 'answerCallbackQuery' else (5, 15)
            response = requests.post(f'https://api.telegram.org/bot{token}/{method}', json=payload, timeout=timeout)
        except requests.ConnectTimeout:
            raise TelegramError('Таймаут подключения к Telegram', retryable=True) from None
        except requests.RequestException:
            raise TelegramError('Нет ответа от Telegram', retryable=True, uncertain=method == 'sendMessage') from None
        try:
            data = response.json()
        except ValueError:
            raise TelegramError('Некорректный ответ Telegram', retryable=True, uncertain=method == 'sendMessage') from None
        if not isinstance(data, dict):
            raise TelegramError('Некорректный ответ Telegram', uncertain=method == 'sendMessage')
        if not response.ok or not data.get('ok'):
            code = int(data.get('error_code') or response.status_code)
            messages = {
                401: 'Неверный или отозванный токен бота',
                403: 'Бот удалён из группы или ему запрещено отправлять сообщения',
                409: 'Бот уже используется другим приложением. Остановите второй обработчик и сохраните настройки заново',
                429: 'Telegram ограничил частоту запросов',
            }
            description = str(data.get('description') or '').replace(token, '[скрыто]')[:200]
            message = messages.get(code, f'Ошибка Telegram {code}: {description}')
            retry_after = min(3600, max(1, float((data.get('parameters') or {}).get('retry_after') or 5)))
            raise TelegramError(message, code, code == 429 or code >= 500, retry_after=retry_after)
        return data.get('result')


class TelegramService:
    def __init__(self, config: AppConfig, history: HistoryStore, cities: CityStore,
                 logger: logging.Logger, on_ack: Callable[[list[dict]], Awaitable[None]], api=None) -> None:
        self.config = config
        self.history = history
        self.cities = cities
        self.logger = logger
        self.on_ack = on_ack
        self.api = api or TelegramAPI()
        self.store = TelegramStore(config.history_db_path, config.telegram_encryption_key)
        self.employees = EmployeeStore(config.history_db_path)
        self.calls = CallStore(history, self.employees)
        self.flows = TelegramFlows(self.store)
        self.private = TelegramPrivate(self)
        self.employees.import_interactions(history)
        self.seen_users = {}
        self.interactions = TelegramInteractions(self)
        self.profile_ui = TelegramProfile(self)
        self.on_change = None
        self.running = False
        self.service_error = None
        self.poll_errors: dict[str, str] = {}
        self.workers: dict[str, tuple[float, asyncio.Task]] = {}
        self.blocked: dict[str, float] = {}
        self.tasks: list[asyncio.Task] = []
        self.delivery_tasks: dict[str, asyncio.Task] = {}
        self._lock_file = None
        self.setup_administrators: dict[tuple[int, int], tuple[float, set[int]]] = {}
        self.setup_replies: dict[tuple[int, int], float] = {}
        self.setup_backoff: dict[int, float] = {}
        self.setup_tasks: dict[str, asyncio.Task] = {}
        self.interaction_limiter = InteractionLimiter()

    async def start(self) -> None:
        if not self.config.telegram_enabled or self.store.cipher is None:
            return
        import fcntl
        lock_file = self.config.history_db_path.parent / 'telegram.lock'
        self._lock_file = lock_file.open('a')
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.service_error = 'Telegram уже запущен другим экземпляром приложения с этой базой'
            self._lock_file.close()
            self._lock_file = None
            self.logger.warning(self.service_error)
            return
        self.running = True
        self.store.recover_sends()
        self.flows.retire_confirmations()
        if self.history.get_runtime_state('telegram_keyboard_version') != 'emoji-v3':
            with self.store.connect() as connection:
                connection.execute("UPDATE telegram_outbox SET edit_synced=0,next_attempt=0 WHERE state='sent'")
            self.history.set_runtime_state('telegram_keyboard_version', 'emoji-v3', str(time.time()))
        self.tasks = [asyncio.create_task(self._supervise()), asyncio.create_task(self._deliver()), asyncio.create_task(self._cleanup()), asyncio.create_task(self.private.run())]

    async def _cleanup(self):
        async def remove(row):
            setting = next((item for item in self.store.settings() if item['bot_id'] == row['bot_id']), None)
            if not setting:
                self.flows.cleanup_result(row)
                return
            try:
                await self.api.call(self.store.token(setting), 'deleteMessage', chat_id=row['chat_id'], message_id=row['message_id'])
                self.flows.cleanup_result(row)
            except TelegramError as exc:
                retry = max(10, exc.retry_after, min(300, 2 ** min(row['attempts'], 8))) if exc.retryable else None
                self.flows.cleanup_result(row, delay=retry)
                if not exc.retryable and 'message to delete not found' not in str(exc).lower():
                    self.logger.warning('Telegram temporary message not deleted: region=%s, %s', setting['area_id'], exc)
            except ValueError:
                self.flows.cleanup_result(row)
        while self.running:
            try:
                await asyncio.gather(*(remove(row) for row in self.flows.cleanup_due()))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.warning('Telegram message cleanup failed; retrying')
            await asyncio.sleep(3)

    async def stop(self) -> None:
        self.running = False
        tasks = self.tasks + [task for _, task in self.workers.values()] + list(self.delivery_tasks.values()) + list(self.setup_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def _setting(self, area_id: str) -> dict | None:
        return next(iter(self.store.settings(area_id)), None)

    def remember_user(self, setting, user):
        if not user or not user.get('id') or user.get('is_bot'):
            return
        user_id = str(user['id'])
        signature = (user.get('first_name'),user.get('last_name'),user.get('username'),setting['area_id'])
        previous = self.seen_users.get(user_id)
        now = time.monotonic()
        if previous and previous[0] == signature and now - previous[1] < 60:
            return
        self.employees.observe(user, setting['area_id'])
        if len(self.seen_users) >= 4096:
            self.seen_users.pop(next(iter(self.seen_users)))
        self.seen_users[user_id] = (signature,now)

    def _city(self, area_id: str):
        return next((city for city in self.cities.list(include_disabled=True) if city.area_id == area_id), None)

    def public_settings(self) -> dict:
        settings = {item['area_id']: item for item in self.store.settings()}
        items = []
        for city in self.cities.list(include_disabled=True):
            setting = settings.get(city.area_id) or {}
            worker = self.workers.get(city.area_id)
            error = setting.get('last_error') or self.poll_errors.get(city.area_id)
            if setting and self.store.cipher:
                try:
                    self.store.token(setting)
                except ValueError as exc:
                    error = str(exc)
            items.append({
                'area_id': city.area_id, 'display_name': city.display_name,
                'enabled': bool(setting.get('enabled')), 'token_configured': bool(setting),
                'require_profile': bool(setting.get('require_profile')),
                'allow_reassign_any': bool(setting.get('allow_reassign_any', True)),
                'bot_username': setting.get('bot_username'), 'chat_id': setting.get('chat_id'),
                'message_thread_id': setting.get('thread_id'), 'chat_title': setting.get('chat_title'),
                'setup_until': setting.get('setup_until') or 0,
                'polling': bool(worker and not worker[1].done()), 'last_error': error,
                'setups': self.store.setups(city.area_id), 'delivery_stats': self.store.delivery_stats(city.area_id),
            })
        return {'items': items, 'key_ready': self.store.cipher is not None,
                'key_error': self.store.key_error, 'enabled': self.config.telegram_enabled,
                'running': self.running, 'error': self.service_error,
                'event_ttl_seconds': self.config.telegram_event_ttl_seconds}

    async def configure(self, area_id: str, enabled: bool, token: str | None) -> None:
        if not self._city(area_id):
            raise ValueError('Локация не найдена')
        if self.store.cipher is None:
            raise ValueError(self.store.key_error)
        token = token.strip() if token else None
        bot = None
        if token:
            if not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,}', token):
                raise ValueError('Некорректный формат токена бота')
            bot = await self.api.call(token, 'getMe')
            if not bot.get('is_bot') or not bot.get('username'):
                raise ValueError('Telegram не подтвердил учётную запись бота')
            webhook = await self.api.call(token, 'getWebhookInfo')
            if webhook.get('url'):
                raise ValueError('У бота настроен webhook другого приложения. Используйте отдельного бота')
        self.store.save(area_id, enabled, token, bot)
        self.poll_errors.pop(area_id, None)

    def enqueue(self, event: dict) -> None:
        if self.config.telegram_enabled and self.store.cipher:
            try:
                self.store.enqueue(event, self.config.telegram_event_ttl_seconds)
                self.private.enqueue(event['id'])
            except Exception:
                self.logger.error('Telegram queue failed for event %s; recovery will retry', event.get('id'))

    async def acknowledge(self, event_ids: list[str], actor: str = 'Веб-монитор', source: str = 'web',
                          user_id: str | None = None) -> list[dict]:
        items = self.calls.silence(event_ids)
        if items:
            await self.on_ack(items)
        return items

    async def test(self, area_id: str) -> dict:
        settings = self._setting(area_id)
        if not settings:
            raise ValueError('Сначала сохраните токен бота')
        if not self.running:
            raise ValueError(self.service_error or 'Telegram отключён в настройках сервера')
        if settings['chat_id'] is None:
            raise ValueError('Сначала отправьте /setup боту в теме и подтвердите привязку')
        token = self.store.token(settings)
        await self.api.call(token, 'getMe')
        city = self._city(area_id)
        message = await self.api.call(token, 'sendMessage', **self._destination(settings),
            text=f'🔔 Тестовое уведомление\n{city.display_name}\nНажмите «Я иду» для проверки.',
            reply_markup={'inline_keyboard': [[{'text': 'Я иду', 'callback_data': 'test'}]]})
        self.store.set_error(area_id, None)
        return {'message_id': message['message_id'], 'message': 'Тест отправлен в выбранную тему'}

    def begin_setup(self, area_id: str) -> None:
        setting = self._setting(area_id)
        if not setting:
            raise ValueError('Сначала сохраните токен бота')
        self.store.token(setting)
        self.store.begin_setup(area_id)
        self.setup_replies = {key: value for key, value in self.setup_replies.items() if key[0] != setting['bot_id']}

    @staticmethod
    def _destination(settings: dict) -> dict:
        result = {'chat_id': settings['chat_id']}
        if settings['thread_id'] is not None:
            result['message_thread_id'] = settings['thread_id']
        return result

    async def _supervise(self) -> None:
        while self.running:
            try:
                for setting in self.store.settings():
                    area_id = setting['area_id']
                    worker = self.workers.get(area_id)
                    if worker and not worker[1].done():
                        continue
                    if self.blocked.get(area_id) == setting['updated_at']:
                        continue
                    task = asyncio.create_task(self._poll(setting))
                    self.workers[area_id] = (setting['updated_at'], task)
            except Exception:
                self.logger.error('Telegram worker refresh failed; retrying')
            await asyncio.sleep(2)

    async def _poll(self, original: dict) -> None:
        area_id = original['area_id']
        revision = original['updated_at']
        while self.running:
            setting = self._setting(area_id)
            if not setting or setting['updated_at'] != revision:
                return
            try:
                token = self.store.token(setting)
                updates = await self.api.call(token, 'getUpdates', offset=self.store.offset(setting['bot_id']),
                                              timeout=10, allowed_updates=['message', 'callback_query'])
                self.poll_errors.pop(area_id, None)
                await self._process_updates(setting, token, updates, defer_setup=True)
            except asyncio.CancelledError:
                raise
            except (TelegramError, ValueError) as exc:
                self.poll_errors[area_id] = str(exc)
                if isinstance(exc, ValueError) or getattr(exc, 'code', 0) in {401, 409}:
                    self.blocked[area_id] = revision
                    self.logger.warning('Telegram polling stopped: region=%s, %s', area_id, exc)
                    return
                await asyncio.sleep(max(5, getattr(exc, 'retry_after', 0)))
            except Exception:
                self.poll_errors[area_id] = 'Ошибка обработки Telegram. Повтор через 5 секунд'
                self.logger.error('Telegram update processing failed: region=%s', area_id)
                await asyncio.sleep(5)

    async def _process_updates(self, setting: dict, token: str, updates: list[dict], defer_setup: bool = False) -> None:
        valid = [update for update in updates if isinstance(update, dict) and isinstance(update.get('update_id'), int)]
        ordered = sorted(valid, key=lambda update: (not bool(update.get('callback_query')), update['update_id']))
        for update in ordered:
            current = self._setting(setting['area_id'])
            if not current or current['updated_at'] != setting['updated_at']:
                return
            try:
                command_parts = str((update.get('message') or {}).get('text') or '').split(maxsplit=1)
                is_setup = bool(command_parts and command_parts[0].lower() in {'/setup', '/setup@' + current['bot_username'].lower()})
                if defer_setup and is_setup:
                    self.remember_user(current, (update.get('message') or {}).get('from'))
                    command = str(update['message'].get('text') or '').split(maxsplit=1)[0:1]
                    if (not command or command[0].lower() not in {'/setup', '/setup@' + current['bot_username'].lower()}
                            or current.get('setup_until', 0) <= time.time()
                            or self.setup_backoff.get(current['bot_id'], 0) > time.monotonic()):
                        continue
                    previous = self.setup_tasks.get(current['area_id'])
                    if previous is None or previous.done():
                        self.setup_tasks[current['area_id']] = asyncio.create_task(self._setup_job(current, token, update))
                else:
                    await self._handle_update(current, token, update)
            except asyncio.CancelledError:
                raise
            except TelegramError as exc:
                if update.get('message'):
                    self.setup_backoff[setting['bot_id']] = time.monotonic() + max(5, exc.retry_after)
                if exc.code != 400:
                    self.logger.warning('Telegram update failed: region=%s, update=%s, %s', setting['area_id'], update['update_id'], exc)
                    self.poll_errors[setting['area_id']] = str(exc)
            except Exception:
                self.logger.warning('Telegram update skipped: region=%s, update=%s', setting['area_id'], update['update_id'])
        if valid:
            self.store.save_offset(setting['bot_id'], max(update['update_id'] for update in valid) + 1)

    async def _setup_job(self, setting: dict, token: str, update: dict) -> None:
        try:
            await self._handle_update(setting, token, update)
        except asyncio.CancelledError:
            raise
        except TelegramError as exc:
            self.setup_backoff[setting['bot_id']] = time.monotonic() + max(5, exc.retry_after)
            self.poll_errors[setting['area_id']] = str(exc)
            self.logger.warning('Telegram setup failed: region=%s, %s', setting['area_id'], exc)
        except ValueError:
            self.logger.info('Telegram setup window closed: region=%s', setting['area_id'])
        except Exception:
            self.logger.warning('Telegram setup command skipped: region=%s', setting['area_id'])

    async def _handle_update(self, setting: dict, token: str, update: dict) -> None:
        query = update.get('callback_query')
        if query:
            await self._callback(setting, token, query)
            return
        message = update.get('message') or {}
        actor = message.get('from') or {}
        allowed, _ = self.interaction_limiter.allow((setting['bot_id'], actor.get('id'), 'message'))
        if not allowed:
            return
        self.remember_user(setting, actor)
        if (message.get('chat') or {}).get('type')=='private' and (message.get('chat') or {}).get('id')==actor.get('id') and not actor.get('is_bot'):
            self.private.register(setting,actor)
        if await self.interactions.message(setting, token, message):
            return
        command = str(message.get('text') or '').split(maxsplit=1)[0:1]
        if not command or command[0].lower() not in {'/setup', '/setup@' + setting['bot_username'].lower()}:
            return
        if setting.get('setup_until', 0) <= time.time():
            return
        if self.setup_backoff.get(setting['bot_id'], 0) > time.monotonic():
            return
        chat = message.get('chat') or {}
        user = message.get('from') or {}
        if chat.get('type') not in {'group', 'supergroup'} or not user.get('id') or user.get('is_bot'):
            return
        thread_id = message.get('message_thread_id')
        if any(proposal['chat_id'] == chat['id'] and proposal['thread_id'] == thread_id for proposal in self.store.setups(setting['area_id'])):
            return
        cache_key = (setting['bot_id'], chat['id'])
        cache = self.setup_administrators.get(cache_key)
        if cache is None or cache[0] < time.monotonic():
            administrators = await self.api.call(token, 'getChatAdministrators', chat_id=chat['id'])
            cache = (time.monotonic() + 60, {admin.get('user', {}).get('id') for admin in administrators})
            if len(self.setup_administrators) >= 512:
                self.setup_administrators.clear()
            self.setup_administrators[cache_key] = cache
        if user['id'] not in cache[1]:
            return
        if self.setup_replies.get(cache_key, 0) > time.monotonic():
            return
        if len(self.setup_replies) >= 512:
            self.setup_replies.clear()
        self.setup_replies[cache_key] = time.monotonic() + 30
        self.store.add_setup(setting, chat, thread_id)
        await self.api.call(token, 'sendMessage', **{'chat_id': chat['id'], **({'message_thread_id': thread_id} if thread_id else {})},
            text='Группа и тема определены. Подтвердите привязку в админке PereHomeLab → Telegram. Предложение действует 15 минут.')

    async def _callback(self, setting: dict, token: str, query: dict) -> None:
        actor = query.get('from') or {}
        allowed, notify = self.interaction_limiter.allow((setting['bot_id'], actor.get('id'), 'callback'))
        if not allowed:
            if notify:
                await self.api.call(token, 'answerCallbackQuery', callback_query_id=query['id'],
                    text='Слишком много действий. Подождите несколько секунд.')
            return
        self.remember_user(setting, query.get('from'))
        if str(query.get('data') or '') != 'test':
            await self.interactions.callback(setting, token, query)
            return
        message = query.get('message') or {}
        actor = query.get('from') or {}
        if (actor.get('id') and not actor.get('is_bot')
                and (message.get('chat') or {}).get('id') == setting['chat_id']
                and message.get('message_thread_id') in {None, setting['thread_id']}):
            name = ' '.join(str(actor.get(key) or '') for key in ('first_name', 'last_name')).strip()[:150]
            results = await asyncio.gather(
                self.api.call(token, 'answerCallbackQuery', callback_query_id=query['id'], text='Проверка успешна'),
                self.api.call(token, 'editMessageText', chat_id=setting['chat_id'], message_id=message['message_id'],
                    text=f'✅ Тест принят\\n{name}', reply_markup={'inline_keyboard': []}), return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    raise result
        else:
            await self.api.call(token, 'answerCallbackQuery', callback_query_id=query['id'], text='Тест недоступен')

    @staticmethod
    def message_text(event: dict) -> str:
        region = html.escape(str(event.get('area_name') or 'Локация')[:100])
        name = html.escape(str(event.get('device_name') or 'Кнопка')[:300])
        pressed = html.escape(display_date(event.get('pressed_at') or event.get('pressed_at_display')))
        if CallStore.assigned(event):
            actor = html.escape(str(event.get('assignee_name') or event.get('acknowledged_by') or '')[:200])
            accepted = html.escape(display_date(event.get('acknowledged_at') or event.get('acknowledged_at_display')))
            source = html.escape(str(event.get('assignee_source_label') or event.get('acknowledged_source') or 'Не указана'))
            text = f'✅ <b>Вызов принят · {region}</b>\nПереговорка: <b>{name}</b>\nНажатие: {pressed}\nПринял: <b>{actor}</b> ({source})\nПринято: {accepted}'
        else:
            text = f'🔔 <b>{region}</b>\nПереговорка: <b>{name}</b>\nНажатие: {pressed}\nОжидает сотрудника'
        for comment in event.get('comments', [])[-3:]:
            text += '\n\n💬 ' + html.escape(comment['author_name'][:150]) + ' · ' + display_date(comment['created_at']) + '\n' + html.escape(comment['body'][:350])
        return text

    async def _deliver(self) -> None:
        last_recovery = 0
        while self.running:
            try:
                if time.monotonic() - last_recovery >= 10:
                    self.store.recover_live(self.config.telegram_event_ttl_seconds)
                    last_recovery = time.monotonic()
                for area_id, task in list(self.delivery_tasks.items()):
                    if task.done():
                        if not task.cancelled() and task.exception():
                            self.logger.error('Telegram delivery task failed: region=%s', area_id)
                        del self.delivery_tasks[area_id]
                while len(self.delivery_tasks) < 8:
                    event = self.store.due(set(self.delivery_tasks))
                    if not event:
                        break
                    self.delivery_tasks[event['area_id']] = asyncio.create_task(self._send(event))
                for accepted in self.store.pending_edits():
                    if len(self.delivery_tasks) >= 8:
                        break
                    if accepted['area_id'] not in self.delivery_tasks:
                        self.delivery_tasks[accepted['area_id']] = asyncio.create_task(self._edit(accepted))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.error('Telegram delivery check failed; retrying')
            await asyncio.sleep(1)

    async def _send(self, event: dict) -> None:
        setting = self._setting(event['area_id'])
        city = self._city(event['area_id'])
        if (not setting or not city or not city.enabled or not setting['enabled']
                or setting['bot_id'] != event['bot_id'] or setting['chat_id'] != event['chat_id']
                or setting['thread_id'] != event['thread_id']):
            self.store.update_delivery(event['event_id'], 'canceled')
            return
        current = self.calls.details(event['event_id'])
        if not current or (self.calls.assigned(current) and not self.private.has_copy(event['event_id'],event['bot_id'])):
            self.store.update_delivery(event['event_id'], 'canceled')
            return
        try:
            token = self.store.token(setting)
            self.store.update_delivery(event['event_id'], 'sending')
            result = await self.api.call(token, 'sendMessage', **self._destination(setting),
                text=self.message_text(current), parse_mode='HTML',
                reply_markup=self.interactions.keyboard(setting, current))
            self.store.update_delivery(event['event_id'], 'sent', message_id=int(result['message_id']))
            self.store.finish_edit(event['event_id'], revision=current['revision'])
            self.store.set_error(event['area_id'], None)
        except asyncio.CancelledError:
            self.store.update_delivery(event['event_id'], 'uncertain', error='Отправка прервана. Повтор отключён для защиты от дублей')
            raise
        except (TelegramError, ValueError) as exc:
            state = 'failed'
            if getattr(exc, 'uncertain', False):
                state = 'uncertain'
            elif getattr(exc, 'retryable', False) and event['attempts'] < 8:
                state = 'queued'
            self.store.update_delivery(event['event_id'], state, error=str(exc),
                                       delay=max(getattr(exc, 'retry_after', 0), min(60, 2 ** (event['attempts'] + 1))))
            self.store.set_error(event['area_id'], str(exc))
        except Exception:
            error = 'Результат отправки неизвестен. Повтор отключён для защиты от дублей'
            self.store.update_delivery(event['event_id'], 'uncertain', error=error)
            self.store.set_error(event['area_id'], error)

    async def _edit(self, event: dict) -> None:
        current = self.calls.details(event['event_id'])
        if not current:
            return
        setting = self._setting(event['area_id'])
        if not setting or setting['bot_id'] != event['bot_id']:
            self.store.finish_edit(event['event_id'], error='Бот локации изменён; старое сообщение не обновлено')
            return
        try:
            await self.api.call(self.store.token(setting), 'editMessageText', chat_id=event['chat_id'],
                message_id=event['message_id'], text=self.message_text(current), parse_mode='HTML',
                reply_markup=self.interactions.keyboard(setting, current))
            self.store.finish_edit(event['event_id'], revision=current['revision'])
            self.store.set_error(event['area_id'], None)
        except TelegramError as exc:
            if exc.code == 400 and 'message is not modified' in str(exc):
                self.store.finish_edit(event['event_id'], revision=current['revision'])
                return
            self.store.finish_edit(event['event_id'], error=str(exc), delay=max(10, exc.retry_after) if exc.retryable else None)
            self.store.set_error(event['area_id'], str(exc))
        except ValueError as exc:
            self.store.finish_edit(event['event_id'], error=str(exc))
            self.store.set_error(event['area_id'], str(exc))
