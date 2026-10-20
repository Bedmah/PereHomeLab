import asyncio
import json
import logging
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from app.cities import CityStore
from app.history import HistoryStore
from app.telegram_service import TelegramAPI, TelegramError, TelegramService
from app.telegram_store import TelegramStore
from app.users import UserStore
from app.watcher import HomeAssistantWatcher, WatchedEntity
from tests.test_core import FakeClient, make_config


def bot_token(bot_id=111111):
    return str(bot_id) + ':' + 'x' * 32


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.failures = {}
        self.message_id = 100
        self.webhook_url = ''

    async def call(self, token, method, **payload):
        self.calls.append((token, method, payload))
        if self.failures.get(method):
            raise self.failures[method].pop(0)
        if method == 'getMe':
            bot_id = int(token.split(':')[0])
            return {'id': bot_id, 'is_bot': True, 'username': f'review_bot_{bot_id}'}
        if method == 'getWebhookInfo':
            return {'url': self.webhook_url}
        if method == 'getChatAdministrators':
            return [{'user': {'id': 7}}]
        if method == 'sendMessage':
            self.message_id += 1
            return {'message_id': self.message_id}
        if method == 'getUpdates':
            await asyncio.sleep(0.01)
            return []
        return True


class TelegramTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = replace(make_config(self.root), telegram_encryption_key=Fernet.generate_key().decode())
        self.history = HistoryStore(self.config.history_db_path)
        self.cities = CityStore(self.config.history_db_path)
        self.cities.upsert('spb', 'Санкт-Петербург')
        self.cities.upsert('msk', 'Москва')
        self.api = FakeTelegram()
        self.broadcasts = []

        async def capture(items):
            self.broadcasts.extend(items)

        self.service = TelegramService(self.config, self.history, self.cities, logging.getLogger('test'), capture, self.api)

    def tearDown(self):
        self.temp.cleanup()

    async def configure(self, area='spb', bot_id=111111, chat_id=-1001, thread_id=42):
        await self.service.configure(area, True, bot_token(bot_id))
        setting = self.service._setting(area)
        setup_id = self.service.store.add_setup(setting, {'id': chat_id, 'title': 'Test group'}, thread_id)
        self.service.store.confirm_setup(area, setup_id)
        return self.service._setting(area)

    def event(self, event_id='event-1', area='spb', when=None, live=True):
        when = when or datetime.now()
        self.history.add_press(event_id, 'binary_sensor.' + area + '_safety', 'Переговорка <1>',
            when.isoformat(timespec='seconds'), when.strftime('%Y-%m-%d %H:%M:%S'), area, area.upper())
        if live:
            self.history.mark_live_notified(event_id)
        return self.history.get_press(event_id)

    def query(self, event_id='event-1', user_id=7, chat_id=-1001, thread_id=42, message_id=None):
        delivery = self.service.store.delivery(event_id)
        return {'id': 'query-' + str(user_id), 'from': {'id': user_id, 'first_name': f'Сотрудник {user_id}'},
                'data': 'claim:' + event_id, 'message': {'message_id': message_id or delivery['message_id'],
                'chat': {'id': chat_id}, 'message_thread_id': thread_id}}

    async def send(self, event=None):
        self.service.enqueue(event or self.event())
        await self.service._send(self.service.store.due())

    async def test_tokens_are_encrypted_and_survive_restart(self):
        setting = await self.configure()
        self.assertNotIn(bot_token(), setting['token_encrypted'])
        reopened = TelegramStore(self.config.history_db_path, self.config.telegram_encryption_key)
        self.assertEqual(reopened.token(setting), bot_token())
        public = json.dumps(self.service.public_settings())
        self.assertNotIn(bot_token(), public)
        self.assertNotIn(setting['token_encrypted'], public)
        wrong_key = TelegramStore(self.config.history_db_path, Fernet.generate_key().decode())
        with self.assertRaisesRegex(ValueError, 'не расшифрован'):
            wrong_key.token(setting)

    async def test_second_process_cannot_poll_same_database(self):
        await self.service.start()
        other = TelegramService(self.config, self.history, self.cities, logging.getLogger('test'), self.service.on_ack, self.api)
        try:
            await other.start()
            self.assertFalse(other.running)
            self.assertIn('другим экземпляром', other.service_error)
        finally:
            await other.stop()
            await self.service.stop()

    async def test_slow_region_does_not_block_delivery_to_another_region(self):
        await self.configure()
        await self.configure('msk', 222222, -2002, 85)
        self.service.enqueue(self.event('slow', 'spb'))
        self.service.enqueue(self.event('fast', 'msk'))
        blocked = asyncio.Event()
        delivered = asyncio.Event()
        original_call = self.api.call

        async def delayed_call(token, method, **payload):
            if method == 'sendMessage' and token == bot_token():
                await blocked.wait()
            result = await original_call(token, method, **payload)
            if method == 'sendMessage' and token == bot_token(222222):
                delivered.set()
            return result

        self.api.call = delayed_call
        self.service.running = True
        self.service.tasks = [asyncio.create_task(self.service._deliver())]
        try:
            await asyncio.wait_for(delivered.wait(), timeout=2)
            self.assertEqual(self.service.store.delivery('fast')['state'], 'sent')
            self.assertEqual(self.service.store.delivery('slow')['state'], 'sending')
        finally:
            await self.service.stop()

    async def test_same_bot_cannot_be_assigned_to_two_regions(self):
        await self.configure()
        with self.assertRaisesRegex(ValueError, 'другого региона'):
            await self.service.configure('msk', True, bot_token())

    async def test_existing_webhook_is_not_deleted(self):
        self.api.webhook_url = 'https://example.invalid/webhook'
        with self.assertRaisesRegex(ValueError, 'webhook'):
            await self.service.configure('spb', True, bot_token())
        self.assertFalse(any(method == 'deleteWebhook' for _, method, _ in self.api.calls))

    async def test_separate_bots_chats_and_topics(self):
        await self.configure()
        await self.configure('msk', 222222, -2002, 85)
        await self.send(self.event('spb-event', 'spb'))
        await self.send(self.event('msk-event', 'msk'))
        sends = [(token, payload) for token, method, payload in self.api.calls if method == 'sendMessage']
        self.assertEqual([(token, data['chat_id'], data['message_thread_id']) for token, data in sends],
            [(bot_token(), -1001, 42), (bot_token(222222), -2002, 85)])
        self.assertIn('&lt;1&gt;', sends[0][1]['text'])

    async def test_setup_requires_group_administrator_and_confirmation(self):
        await self.service.configure('spb', False, bot_token())
        setting = self.service._setting('spb')
        message = {'text': '/setup@review_bot_111111', 'chat': {'id': -1001, 'type': 'supergroup', 'title': 'Office'},
                   'message_thread_id': 42, 'from': {'id': 8}}
        await self.service._handle_update(setting, bot_token(), {'message': message})
        self.assertEqual(self.service.store.setups('spb'), [])
        message['from']['id'] = 7
        await self.service._handle_update(setting, bot_token(), {'message': message})
        proposals = self.service.store.setups('spb')
        self.assertEqual(len(proposals), 1)
        self.assertIsNone(self.service._setting('spb')['chat_id'])
        self.service.store.confirm_setup('spb', proposals[0]['id'])
        self.assertEqual(self.service._setting('spb')['thread_id'], 42)

    def setup_update(self, update_id, thread_id=42):
        return {'update_id': update_id, 'message': {'text': '/setup@review_bot_111111',
            'chat': {'id': -1001, 'type': 'supergroup', 'title': 'Office'},
            'message_thread_id': thread_id, 'from': {'id': 7}}}

    async def test_setup_spam_after_binding_does_not_call_api_or_change_route(self):
        setting = await self.configure()
        original_calls = len(self.api.calls)
        await self.service._process_updates(setting, bot_token(), [self.setup_update(index, 999) for index in range(100, 200)])
        self.assertEqual(len(self.api.calls), original_calls)
        self.assertEqual(self.service._setting('spb')['thread_id'], 42)
        self.assertEqual(self.service.store.setups('spb'), [])
        self.assertEqual(self.service.store.offset(111111), 200)

    async def test_setup_spam_while_registration_open_only_replies_once(self):
        await self.configure()
        self.service.begin_setup('spb')
        setting = self.service._setting('spb')
        original_calls = len(self.api.calls)
        await self.service._process_updates(setting, bot_token(), [self.setup_update(index) for index in range(100, 200)])
        calls = self.api.calls[original_calls:]
        self.assertEqual(sum(method == 'getChatAdministrators' for _, method, _ in calls), 1)
        self.assertEqual(sum(method == 'sendMessage' for _, method, _ in calls), 1)
        proposals = self.service.store.setups('spb')
        self.assertEqual(len(proposals), 1)
        first_id = proposals[0]['id']
        await self.service._process_updates(setting, bot_token(), [self.setup_update(201)])
        self.assertEqual(self.service.store.setups('spb')[0]['id'], first_id)
        self.service.store.confirm_setup('spb', first_id)
        self.service.store.confirm_setup('spb', first_id)
        self.assertEqual(self.service._setting('spb')['setup_until'], 0)

    async def test_callback_has_priority_over_setup_spam(self):
        await self.configure()
        await self.send()
        self.service.begin_setup('spb')
        setting = self.service._setting('spb')
        original_calls = len(self.api.calls)
        updates = [self.setup_update(index) for index in range(100, 200)]
        updates.append({'update_id': 200, 'callback_query': self.query()})
        await self.service._process_updates(setting, bot_token(), updates)
        calls = self.api.calls[original_calls:]
        self.assertEqual(calls[0][1], 'answerCallbackQuery')
        self.assertEqual(self.history.get_press('event-1')['acknowledged_source'], 'telegram')
        self.assertEqual(self.service.store.offset(111111), 201)

    async def test_slow_setup_request_does_not_block_next_batch_callback(self):
        await self.configure()
        await self.send()
        self.service.begin_setup('spb')
        setting = self.service._setting('spb')
        started = asyncio.Event()
        release = asyncio.Event()
        original_call = self.api.call

        async def slow_call(token, method, **payload):
            if method == 'getChatAdministrators':
                started.set()
                await release.wait()
            return await original_call(token, method, **payload)

        self.api.call = slow_call
        try:
            await self.service._process_updates(setting, bot_token(), [self.setup_update(100)], defer_setup=True)
            await asyncio.wait_for(started.wait(), timeout=2)
            await asyncio.wait_for(self.service._process_updates(setting, bot_token(), [
                {'update_id': 101, 'callback_query': self.query()}], defer_setup=True), timeout=2)
            self.assertEqual(self.history.get_press('event-1')['acknowledged_source'], 'telegram')
            self.assertFalse(self.service.setup_tasks['spb'].done())
        finally:
            await self.service.stop()

    async def test_bad_callback_does_not_poison_remaining_updates(self):
        setting = await self.configure()
        await self.send()
        await self.send(self.event('event-2', when=datetime.now() + timedelta(seconds=5)))
        self.api.failures['answerCallbackQuery'] = [TelegramError('Query is too old', 400)]
        await self.service._process_updates(setting, bot_token(), [
            {'update_id': 100, 'callback_query': self.query()},
            {'update_id': 101, 'callback_query': self.query('event-2', user_id=8)},
        ])
        self.assertEqual(len(self.broadcasts), 2)
        self.assertEqual(self.service.store.offset(111111), 102)

    async def test_setup_rate_limit_does_not_stall_batch(self):
        await self.configure()
        self.service.begin_setup('spb')
        setting = self.service._setting('spb')
        original_calls = len(self.api.calls)
        self.api.failures['getChatAdministrators'] = [TelegramError('Rate limit', 429, True, retry_after=30)]
        await self.service._process_updates(setting, bot_token(), [self.setup_update(index) for index in range(100, 200)])
        self.assertEqual(sum(method == 'getChatAdministrators' for _, method, _ in self.api.calls[original_calls:]), 1)
        self.assertEqual(self.service.store.offset(111111), 200)

    async def test_callback_without_optional_thread_id_still_matches_registered_message(self):
        setting = await self.configure()
        await self.send()
        query = self.query()
        del query['message']['message_thread_id']
        await self.service._callback(setting, bot_token(), query)
        self.assertEqual(self.history.get_press('event-1')['acknowledged_source'], 'telegram')

    async def test_test_callback_without_thread_id_and_with_expired_answer_still_edits(self):
        setting = await self.configure()
        self.service.running = True
        result = await self.service.test('spb')
        self.api.failures['answerCallbackQuery'] = [TelegramError('Query is too old', 400)]
        query = {'id': 'old-test-query', 'data': 'test', 'from': {'id': 7, 'first_name': 'Test'},
                 'message': {'chat': {'id': -1001}, 'message_id': result['message_id']}}
        await self.service._process_updates(setting, bot_token(), [{'update_id': 200, 'callback_query': query}])
        self.assertTrue(any(method == 'editMessageText' for _, method, _ in self.api.calls))
        self.assertEqual(self.service.store.offset(111111), 201)

    async def test_reconfirming_same_topic_keeps_live_queue(self):
        setting = await self.configure()
        self.service.enqueue(self.event())
        self.service.begin_setup('spb')
        setup_id = self.service.store.add_setup(self.service._setting('spb'), {'id': -1001, 'title': 'Office'}, 42)
        self.service.store.confirm_setup('spb', setup_id)
        self.assertEqual(self.service._setting('spb')['enabled_since'], setting['enabled_since'])
        self.assertEqual(self.service.store.delivery('event-1')['state'], 'queued')

    async def test_history_import_and_old_live_events_do_not_send(self):
        await self.configure()
        self.event('historical', live=False)
        self.service.store.recover_live(300)
        self.assertIsNone(self.service.store.due())
        self.service.enqueue(self.event('old', when=datetime.now() - timedelta(minutes=20)))
        self.assertIsNone(self.service.store.delivery('old'))

    async def test_duplicate_event_only_has_one_delivery(self):
        await self.configure()
        event = self.event()
        self.service.enqueue(event)
        self.service.enqueue(event)
        await self.service._send(self.service.store.due())
        self.assertIsNone(self.service.store.due())
        self.assertEqual(sum(method == 'sendMessage' for _, method, _ in self.api.calls), 1)

    async def test_callback_records_employee_and_edits_message(self):
        setting = await self.configure()
        await self.send()
        await self.service._callback(setting, bot_token(), self.query())
        saved = self.history.get_press('event-1')
        self.assertEqual(saved['acknowledged_by'], 'Сотрудник 7')
        self.assertEqual(saved['acknowledged_source'], 'telegram')
        self.assertEqual(saved['acknowledged_user_id'], '7')
        self.assertEqual(len(self.broadcasts), 1)
        await self.service._edit(self.service.store.pending_edits()[0])
        edits = [payload for _, method, payload in self.api.calls if method == 'editMessageText']
        self.assertEqual(edits[0]['reply_markup']['inline_keyboard'][0][1]['text'], '↩️ Отменить')
        self.assertIn('Сотрудник 7', edits[0]['text'])

    async def test_simultaneous_employee_claims_apply_in_order(self):
        setting = await self.configure()
        await self.send()
        await asyncio.gather(self.service._callback(setting, bot_token(), self.query(user_id=7)),
                             self.service._callback(setting, bot_token(), self.query(user_id=8)))
        self.assertEqual(len(self.broadcasts), 2)
        answers = [payload['text'] for _, method, payload in self.api.calls if method == 'answerCallbackQuery']
        self.assertEqual(sum(answer == 'Вы приняли вызов' for answer in answers), 2)
        self.assertEqual(self.history.get_press('event-1')['acknowledged_user_id'], '8')

    async def test_claim_is_atomic_across_separate_database_connections(self):
        self.event()
        def claim(actor):
            store = HistoryStore(self.config.history_db_path)
            now = datetime.now().isoformat(timespec='seconds')
            return store.claim(['event-1'], now, now, actor, 'telegram')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ['one', 'two']))
        self.assertEqual(sum(len(items) for items in results), 1)

    async def test_forged_chat_topic_message_and_bot_cannot_claim(self):
        setting = await self.configure()
        await self.send()
        for query in [self.query(chat_id=-999), self.query(thread_id=999), self.query(message_id=999)]:
            await self.service._callback(setting, bot_token(), query)
        await self.service._callback({**setting, 'bot_id': 999999}, bot_token(), self.query())
        self.assertIsNone(self.history.get_press('event-1')['acknowledged_at'])

    async def test_web_silence_does_not_prevent_telegram_assignment(self):
        setting = await self.configure()
        await self.send()
        await self.service.acknowledge(['event-1'])
        await self.service._callback(setting, bot_token(), self.query())
        self.assertEqual(self.history.get_press('event-1')['acknowledged_source'], 'telegram')
        self.assertEqual(len(self.broadcasts), 2)
        self.assertEqual(len(self.service.store.pending_edits()), 1)

    async def test_web_silence_keeps_unsent_notification(self):
        await self.configure()
        self.service.enqueue(self.event())
        await self.service.acknowledge(['event-1'])
        self.assertIsNotNone(self.service.store.due())
        self.assertEqual(self.service.store.delivery('event-1')['state'], 'queued')

    async def test_rate_limit_retries_without_blocking_watcher(self):
        await self.configure()
        self.api.failures['sendMessage'] = [TelegramError('Rate limit', 429, True, retry_after=30)]
        await self.send()
        delivery = self.service.store.delivery('event-1')
        self.assertEqual(delivery['state'], 'queued')
        self.assertGreater(delivery['next_attempt'], time.time() + 25)

    async def test_ambiguous_timeout_is_not_blindly_repeated(self):
        await self.configure()
        self.api.failures['sendMessage'] = [TelegramError('Timeout', retryable=True, uncertain=True)]
        await self.send()
        self.assertEqual(self.service.store.delivery('event-1')['state'], 'uncertain')
        self.assertIsNone(self.service.store.due())

    async def test_callback_can_recover_delivery_with_unknown_http_result(self):
        setting = await self.configure()
        self.api.failures['sendMessage'] = [TelegramError('Timeout', retryable=True, uncertain=True)]
        await self.send()
        await self.service._callback(setting, bot_token(), self.query(message_id=101))
        self.assertEqual(self.history.get_press('event-1')['acknowledged_source'], 'telegram')

    async def test_failed_callback_answer_does_not_lose_browser_acknowledgement(self):
        setting = await self.configure()
        await self.send()
        self.api.failures['answerCallbackQuery'] = [TelegramError('Timeout', retryable=True)]
        with self.assertRaises(TelegramError):
            await self.service._callback(setting, bot_token(), self.query())
        self.assertEqual(len(self.broadcasts), 1)

    async def test_disable_or_route_change_cancels_queued_message(self):
        await self.configure()
        self.service.enqueue(self.event())
        self.service.store.save('spb', False, None, None)
        self.assertIsNone(self.service.store.due())
        self.assertEqual(self.service.store.delivery('event-1')['state'], 'canceled')

    async def test_expired_queued_event_is_not_sent(self):
        await self.configure()
        self.service.enqueue(self.event())
        with self.service.store.connect() as connection:
            connection.execute('UPDATE telegram_outbox SET expires_at = ?', (time.time() - 1,))
        self.assertIsNone(self.service.store.due())
        self.assertEqual(self.service.store.delivery('event-1')['state'], 'expired')

    async def test_test_message_does_not_create_real_press(self):
        setting = await self.configure()
        self.service.running = True
        result = await self.service.test('spb')
        query = {'id': 'test-query', 'data': 'test', 'from': {'id': 7, 'first_name': 'Test'},
                 'message': {'chat': {'id': -1001}, 'message_id': result['message_id'], 'message_thread_id': 42}}
        await self.service._callback(setting, bot_token(), query)
        self.assertEqual(self.history.page(1, 10)['total'], 0)

    async def test_recovery_keeps_offset_and_does_not_repeat_inflight_send(self):
        await self.configure()
        self.service.enqueue(self.event())
        self.service.store.update_delivery('event-1', 'sending')
        self.service.store.save_offset(111111, 55)
        reopened = TelegramStore(self.config.history_db_path, self.config.telegram_encryption_key)
        reopened.recover_sends()
        self.assertEqual(reopened.offset(111111), 55)
        self.assertEqual(reopened.delivery('event-1')['state'], 'uncertain')

    async def test_recent_live_delivery_recovers_after_queue_write_gap(self):
        await self.configure()
        self.event()
        self.service.store.recover_live(300)
        self.assertIsNotNone(self.service.store.due())

    async def test_history_then_live_event_notifies_once(self):
        watcher = HomeAssistantWatcher(self.config, FakeClient(), self.history, self.cities, logging.getLogger('test'))
        entity = WatchedEntity('binary_sensor.button_safety', 'Кнопка', 'spb', 'SPB')
        watcher._entities = {entity.entity_id: entity}
        when = datetime.now(timezone.utc).replace(microsecond=0)
        rows = [[{'entity_id': entity.entity_id, 'state': 'on', 'last_changed': when.isoformat()}]]
        watcher._import_history_rows(rows, datetime.now().astimezone().tzinfo, when - timedelta(seconds=10))
        messages = []
        async def capture(payload):
            messages.append(payload)
        watcher._broadcast = capture
        raw = json.dumps({'type': 'event', 'event': {'data': {'entity_id': entity.entity_id,
            'old_state': {'state': 'off'}, 'new_state': {'state': 'on', 'last_changed': when.isoformat(),
            'attributes': {'friendly_name': 'Кнопка'}}}}})
        await watcher._handle_ws_message(raw)
        await watcher._handle_ws_message(raw)
        self.assertEqual(sum(message.get('type') == 'press' for message in messages), 1)
        self.assertEqual(self.history.page(1, 10)['total'], 1)

    async def test_owner_old_password_stops_working_after_env_change(self):
        users = UserStore(self.root / 'users.db')
        users.ensure_owner('owner', 'old-password')
        users.ensure_owner('owner', 'new-password')
        self.assertIsNone(users.authenticate('owner', 'old-password'))
        self.assertIsNotNone(users.authenticate('owner', 'new-password'))


if __name__ == '__main__':
    unittest.main()
