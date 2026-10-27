import html
import time

from app.call_store import StaleCall


class TelegramInteractions:
    def __init__(self, service):
        self.service = service
        self.links = {}

    def identity(self, setting, user):
        if not user.get('id') or user.get('is_bot'):
            raise ValueError('Действие доступно только сотрудникам')
        profile = self.service.employees.by_user(user['id'])
        if profile:
            if not self.service.employees.area_enabled(profile['id'], setting['area_id']):
                raise ValueError('Доступ к боту этой локации запрещён администратором')
            if profile['blocked']:
                raise ValueError('Профиль заблокирован администратором')
            if profile['allowed_areas'] is not None and setting['area_id'] not in profile['allowed_areas']:
                raise ValueError('Нет доступа к этой локации')
            if setting.get('require_profile') and not profile['profile_complete']:
                raise ValueError('Сначала заполните профиль: /sync в личном чате бота')
            return profile['display_name']
        if setting.get('require_profile'):
            raise ValueError('Сначала заполните профиль: /sync в личном чате бота')
        return (' '.join(str(user.get(key) or '') for key in ('first_name', 'last_name')).strip()
                or str(user.get('username') or user['id']))[:150]

    @staticmethod
    def keyboard(setting, event):
        event_id = event['id']
        name = str(event.get('assignee_name') or event.get('acknowledged_by') or 'сотрудник').split(' · ')[0]
        name = name[:24] + ('…' if len(name) > 24 else '')
        claim = f'✅ Я иду · {name}' if event['call_state'] == 'accepted' else '🚶 Я иду'
        rows = [[{'text': claim, 'callback_data': f'claim:{event_id}'},
                 {'text': '↩️ Отменить', 'callback_data': f'release:{event_id}'}],
                [{'text': '💬 Добавить комментарий', 'callback_data': f'comment:{event_id}'}]]
        return {'inline_keyboard': rows}

    async def changed(self, item, action):
        if action in {'claim', 'takeover'}:
            await self.service.on_ack([item])
        if self.service.on_change:
            await self.service.on_change([item], action)

    async def prompt(self, setting, token, user, message, kind, event=None, action=None, error=None):
        service = self.service
        private = message['chat'].get('type')=='private'
        if private:
            service.profile_ui.cancel(setting,user)
        flow = service.flows.pending(setting['bot_id'], user['id'], kind, event['id'] if event else None)
        if flow:
            service.flows.update(flow['id'], step='retired', done=True)
            service.flows.delete_later(flow['bot_id'], flow['chat_id'], flow['prompt_id'])
        flow = service.flows.create(setting, user['id'], kind, message['chat']['id'],
            event_id=event['id'] if event else None, revision=event['revision'] if event else None,
            thread_id=None if private else message.get('message_thread_id') or setting['thread_id'], data={'action': action})
        name = html.escape(str(user.get('first_name') or 'Сотрудник')[:100])
        text = (f'<a href="tg://user?id={user["id"]}">{name}</a>, '
                f'комментарий к вызову «{html.escape(event["device_name"][:300])}».\n'
                'Введите текст ответом на это сообщение (до 2000 символов).\n'
                'Отмена ввода: /cancel. Запрос исчезнет после ответа или через 15 минут.')
        if error:
            text = html.escape(error) + '\n\n' + text
        destination = {'chat_id': flow['chat_id']}
        if flow['thread_id'] is not None:
            destination['message_thread_id'] = flow['thread_id']
        try:
            sent = await service.api.call(token, 'sendMessage', **destination, text=text,
                parse_mode='HTML', reply_markup={'force_reply': True, 'selective': True,
                    'input_field_placeholder': 'Комментарий к вызову или /cancel'},
                reply_parameters={'message_id': message['message_id']})
            service.flows.update(flow['id'], prompt_id=sent['message_id'])
        except Exception:
            service.flows.update(flow['id'], done=True)
            raise

    async def callback(self, setting, token, query):
        service = self.service
        answer = 'Действие недоступно'
        message = query.get('message') or {}
        user = query.get('from') or {}
        data = str(query.get('data') or '')
        flow = None
        try:
            if not user.get('id') or user.get('is_bot'):
                raise ValueError('Действие недоступно')
            parts = data.split(':')
            action = parts[0]
            if action in {'confirm', 'abort', 'profile', 'pdelete'}:
                flow = service.flows.get(parts[1]) if len(parts) == 2 else None
                if (not flow or flow['bot_id'] != setting['bot_id'] or flow['user_id'] != str(user['id'])
                        or flow['chat_id'] != (message.get('chat') or {}).get('id')
                        or flow['prompt_id'] != message.get('message_id')):
                    raise ValueError('Это чужой или устаревший запрос')
                if action == 'abort':
                    service.flows.update(flow['id'], done=True)
                    await self.finish(token, flow, 'Ввод отменён')
                    if flow['kind'] in {'profile','profile_delete'}:
                        await service.profile_ui.show(token,user,'Ввод отменён')
                    answer = 'Отменено'
                elif (action == 'profile' and flow['kind'] == 'profile') or (action == 'pdelete' and flow['kind'] == 'profile_delete'):
                    if flow['chat_id'] != user['id']:
                        raise ValueError('Профиль меняется только в личном чате бота')
                    answer = await service.profile_ui.confirm(setting,token,user,flow)
                else:
                    raise ValueError('Запрос устарел')
            elif action in {'claim', 'take', 'release', 'comment'} and len(parts) in {2, 3}:
                event = service.calls.get(parts[1])
                private = (message.get('chat') or {}).get('type')=='private'
                if private:
                    if message['chat'].get('id') != user['id']:
                        raise ValueError('Это сообщение другого сотрудника')
                    service.private.eligible(setting,user['id'])
                delivery = service.private.delivery(parts[1],setting['bot_id'],user['id']) if private else service.store.delivery(parts[1])
                if (not event or not delivery or delivery['bot_id'] != setting['bot_id']
                        or delivery['area_id'] != setting['area_id']
                        or (not private and (delivery['chat_id'] != (message.get('chat') or {}).get('id')
                            or message.get('message_thread_id') not in {None, delivery['thread_id']}))):
                    raise ValueError('Вызов не найден в этой локации')
                if delivery['message_id'] is None and delivery['state'] in {'sending', 'uncertain'}:
                    if private:
                        service.private.update(delivery,'sent',message_id=message['message_id'])
                        delivery = service.private.delivery(event['id'],setting['bot_id'],user['id'])
                    else:
                        service.store.update_delivery(event['id'], 'sent', message_id=message['message_id'])
                        delivery = service.store.delivery(event['id'])
                if delivery['message_id'] != message.get('message_id'):
                    raise ValueError('Сообщение не соответствует вызову')
                name = self.identity(setting, user)
                if action in {'claim', 'take', 'release'}:
                    operation = 'release' if action == 'release' else 'assign'
                    item = service.calls.change(event['id'], operation, str(user['id']), name,
                        receipt=f"{setting['bot_id']}:{query['id']}",
                        allow_reassign=bool(setting.get('allow_reassign_any', True)))
                    if item:
                        await self.changed(item, 'release' if operation == 'release' else 'claim')
                    answer = ('Вызов снова ожидает сотрудника' if operation == 'release' else 'Вы приняли вызов') if item else 'Уже применено'
                elif action == 'comment':
                    await self.prompt(setting, token, user, message, 'comment', event)
                    answer = 'Ответьте на сообщение бота'
        except (ValueError, KeyError, IndexError) as exc:
            answer = str(exc) or 'Запрос устарел'
            if isinstance(exc, StaleCall) and flow:
                service.flows.update(flow['id'], done=True)
        await service.api.call(token, 'answerCallbackQuery', callback_query_id=query['id'], text=answer[:200])

    async def finish(self, token, flow, text):
        if flow['kind'] in {'comment', 'confirm'}:
            self.service.flows.delete_later(flow['bot_id'], flow['chat_id'], flow['prompt_id'])
            self.service.flows.update(flow['id'], step='retired', done=True)
            return
        await self.service.api.call(token, 'editMessageText', chat_id=flow['chat_id'],
            message_id=flow['prompt_id'], text=text, reply_markup={'inline_keyboard': []})

    async def start_profile(self, setting, token, user):
        await self.service.profile_ui.start(setting, token, user)

    async def message(self, setting, token, message):
        service = self.service
        user = message.get('from') or {}
        chat = message.get('chat') or {}
        text = str(message.get('text') or '').strip()
        if not user.get('id') or user.get('is_bot'):
            return False
        command = text.split(maxsplit=1)[0].lower() if text else ''
        sync = command in {'/sync', '/sync@' + setting['bot_username'].lower()}
        private = chat.get('type') == 'private' and chat.get('id') == user['id']
        if sync and not private:
            if chat.get('id') != setting['chat_id'] or message.get('message_thread_id') != setting['thread_id']:
                return True
            key = (setting['bot_id'], chat['id'])
            if self.links.get(key, 0) < time.monotonic():
                if len(self.links) > 512:
                    self.links.clear()
                self.links[key] = time.monotonic() + 30
                await service.api.call(token, 'sendMessage', **service._destination(setting),
                    text='Укажите корпоративный логин и ФИО в личном чате бота.',
                    reply_markup={'inline_keyboard': [[{'text': 'Заполнить профиль', 'url': f"https://t.me/{setting['bot_username']}?start=profile"}]]})
            return True
        reply = message.get('reply_to_message') or {}
        if command in {'/cancel', '/cancel@' + setting['bot_username'].lower()}:
            for pending in service.flows.comments_for_user(setting['bot_id'], user['id'], chat.get('id')):
                if message.get('message_thread_id') in {None, pending['thread_id']}:
                    service.flows.update(pending['id'], done=True)
                    await self.finish(token, pending, 'Ввод отменён')
            service.flows.delete_later(setting['bot_id'], chat.get('id'), message.get('message_id'))
            if private:
                await service.profile_ui.message(setting,token,user,text)
            return True
        if private and await service.private.message(setting,token,user,text):
            return True
        menus = {service.profile_ui.SHOW,service.profile_ui.EDIT,service.profile_ui.FILL,service.profile_ui.DELETE,service.profile_ui.CANCEL}
        if private and (command.startswith('/') or text in menus):
            for pending in service.flows.comments_for_user(setting['bot_id'],user['id'],chat['id']):
                service.flows.update(pending['id'],done=True)
                await self.finish(token,pending,'Ввод отменён')
            return await service.profile_ui.message(setting,token,user,text)
        if not reply.get('message_id') and not private:
            return False
        flow = service.flows.for_reply(setting['bot_id'], chat.get('id'), reply.get('message_id'))
        if not flow:
            flow = service.flows.for_original_reply(setting['bot_id'], user['id'], chat.get('id'), reply.get('message_id'))
        if not flow:
            if private:
                pending = service.flows.comments_for_user(setting['bot_id'],user['id'],chat['id'])
                flow = pending[-1] if pending and not reply.get('message_id') else None
                if not flow:
                    return await service.profile_ui.message(setting,token,user,text)
            else:
                return False
        if flow['user_id'] != str(user['id']):
            return True
        if message.get('message_thread_id') not in {None, flow['thread_id']}:
            return True
        try:
            event = service.calls.get(flow['event_id'])
            if not event or event['area_id'] != setting['area_id']:
                raise ValueError('Локация вызова изменена')
            if private:
                service.private.eligible(setting,user['id'])
            name = self.identity(setting, user)
            item = service.calls.comment(flow['event_id'], str(user['id']), name, text,
                f"{setting['bot_id']}:{chat['id']}:{message['message_id']}")
            service.flows.update(flow['id'], done=True)
            await self.finish(token, flow, 'Комментарий сохранён: ' + text)
            service.flows.delete_later(setting['bot_id'], chat['id'], message['message_id'], delay=5)
            service.logger.info('Telegram comment saved: region=%s, event=%s', setting['area_id'], flow['event_id'])
            await self.changed(item, 'comment')
        except ValueError as exc:
            delivery = service.private.delivery(flow['event_id'],setting['bot_id'],user['id']) if private else service.store.delivery(flow['event_id'])
            if delivery and event:
                original = {'chat': chat, 'message_id': delivery['message_id'], 'message_thread_id': flow['thread_id']}
                try:
                    if private:
                        service.private.eligible(setting,user['id'])
                    await self.prompt(setting, token, user, original, 'comment', event, error=str(exc))
                except ValueError:
                    service.flows.update(flow['id'],done=True)
                    await self.finish(token,flow,'Доступ ограничен')
                    if private:
                        await service.profile_ui.send(token,user,str(exc))
        return True
