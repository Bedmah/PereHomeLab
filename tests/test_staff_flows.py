import asyncio
import unittest
from unittest.mock import patch

from app.call_store import StaleCall
from app.dates import display_date
from tests import test_telegram as fixtures
from tests.test_telegram import bot_token


class StaffFlowsTest(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.TelegramTestCase.setUp
    tearDown = fixtures.TelegramTestCase.tearDown
    configure = fixtures.TelegramTestCase.configure
    event = fixtures.TelegramTestCase.event
    query = fixtures.TelegramTestCase.query
    send = fixtures.TelegramTestCase.send

    async def callback(self, setting, data, user=7, flow=None):
        query = ({'from': {'id': user}, 'message': {'message_id': flow['prompt_id'],
            'chat': {'id': flow['chat_id']}}} if flow else self.query(user_id=user))
        query['data'] = data
        query['id'] = data + str(user)
        if flow:
            query['message']['message_id'] = flow['prompt_id']
            query['message']['chat']['id'] = flow['chat_id']
        await self.service._callback(setting, bot_token(), query)

    async def test_release_takeover_and_audit_preserve_comments(self):
        setting = await self.configure()
        await self.send()
        sent_count = sum(method == 'sendMessage' for _, method, _ in self.api.calls)
        await self.callback(setting, 'claim:event-1')
        await self.callback(setting, 'claim:event-1', user=8)
        item = self.service.calls.get('event-1')
        self.assertEqual(item['acknowledged_user_id'], '8')
        self.service.calls.comment('event-1', '8', 'Сотрудник 8', 'Заменили кабель', 'comment1')
        await self.callback(setting, 'release:event-1', user=7)
        item = self.service.calls.details('event-1')
        self.assertEqual(item['call_state'], 'waiting')
        self.assertTrue(item['sound_silenced'])
        self.assertEqual(len(item['comments']), 1)
        self.assertEqual([action['action'] for action in item['actions']], ['claim', 'takeover', 'comment', 'release'])
        await self.callback(setting, 'claim:event-1', user=9)
        self.assertEqual(self.service.calls.get('event-1')['acknowledged_user_id'], '9')
        self.assertEqual(sum(method == 'sendMessage' for _, method, _ in self.api.calls), sent_count)

    async def test_buttons_stay_identical_after_assignment_and_release(self):
        setting = await self.configure()
        await self.send()
        first = self.service.interactions.keyboard(setting, self.service.calls.get('event-1'))
        await self.callback(setting, 'claim:event-1')
        second = self.service.interactions.keyboard(setting, self.service.calls.get('event-1'))
        await self.callback(setting, 'release:event-1')
        third = self.service.interactions.keyboard(setting, self.service.calls.get('event-1'))
        self.assertEqual(first, third)
        self.assertNotEqual(first, second)
        self.assertTrue(second['inline_keyboard'][0][0]['text'].startswith('✅'))
        self.assertIn('Сотрудник 7', second['inline_keyboard'][0][0]['text'])
        self.assertEqual(first['inline_keyboard'][0][0]['callback_data'], second['inline_keyboard'][0][0]['callback_data'])
        self.assertEqual([button['text'] for row in first['inline_keyboard'] for button in row],
                         ['🚶 Я иду', '↩️ Отменить', '💬 Добавить комментарий'])

    async def test_repeated_claim_by_same_person_does_not_add_actions(self):
        setting = await self.configure()
        await self.send()
        await self.callback(setting, 'claim:event-1')
        query = self.query()
        query['id'] = 'another-callback'
        await self.service._callback(setting, bot_token(), query)
        self.assertEqual(len(self.service.calls.details('event-1')['actions']), 1)
        self.assertEqual(len(self.broadcasts), 1)

    async def test_same_callback_cannot_reclaim_after_release(self):
        setting = await self.configure()
        await self.send()
        query = self.query()
        await self.service._callback(setting, bot_token(), query)
        self.service.calls.change('event-1', 'release', '8', 'Сотрудник 8', 1)
        await self.service._callback(setting, bot_token(), query)
        self.assertEqual(self.service.calls.get('event-1')['call_state'], 'waiting')

    async def test_comment_only_correct_user_reply_and_deduplicated(self):
        setting = await self.configure()
        await self.send()
        await self.callback(setting, 'comment:event-1')
        prompt = [payload for _, method, payload in self.api.calls if method == 'sendMessage'][-1]
        self.assertTrue(prompt['reply_markup']['force_reply'])
        self.assertTrue(prompt['reply_markup']['selective'])
        flow = self.service.flows.pending(setting['bot_id'], 7, 'comment', 'event-1')
        message = {'message_id': 500, 'chat': {'id': -1001, 'type': 'supergroup'}, 'message_thread_id': 42,
            'text': 'Починил монитор', 'from': {'id': 8, 'first_name': 'Другой'},
            'reply_to_message': {'message_id': flow['prompt_id']}}
        await self.service._handle_update(setting, bot_token(), {'message': message})
        self.assertEqual(self.service.calls.details('event-1')['comments'], [])
        message['from']['id'] = 7
        unrelated = {**message, 'reply_to_message': {}}
        await self.service._handle_update(setting, bot_token(), {'message': unrelated})
        self.assertEqual(self.service.calls.details('event-1')['comments'], [])
        for _ in range(2):
            await self.service._handle_update(setting, bot_token(), {'message': message})
        self.assertEqual(len(self.service.calls.details('event-1')['comments']), 1)
        with self.service.store.connect() as connection:
            queued = [row['message_id'] for row in connection.execute('SELECT message_id FROM telegram_cleanup')]
        self.assertIn(flow['prompt_id'], queued)
        self.assertIn(500, queued)

    async def test_reply_to_original_notification_saves_comment(self):
        setting = await self.configure()
        await self.send()
        await self.callback(setting, 'comment:event-1')
        await self.service._process_updates(setting, bot_token(), [{'update_id': 101, 'message': {
            'message_id': 501, 'chat': {'id': -1001, 'type': 'supergroup'}, 'message_thread_id': 42,
            'from': {'id': 7}, 'text': 'Работает',
            'reply_to_message': {'message_id': self.service.store.delivery('event-1')['message_id']}}}], defer_setup=True)
        self.assertEqual(len(self.service.calls.details('event-1')['comments']), 1)

    async def test_cancel_comment_removes_prompt_without_cancelling_call(self):
        setting = await self.configure()
        await self.send()
        await self.callback(setting, 'claim:event-1')
        await self.callback(setting, 'comment:event-1')
        flow = self.service.flows.pending(setting['bot_id'], 7, 'comment', 'event-1')
        await self.service._handle_update(setting, bot_token(), {'message': {'message_id': 600,
            'chat': {'id': -1001, 'type': 'supergroup'}, 'message_thread_id': 42,
            'from': {'id': 7}, 'text': '/cancel'}})
        self.assertIsNone(self.service.flows.get(flow['id']))
        self.assertEqual(self.service.calls.get('event-1')['call_state'], 'accepted')
        self.assertEqual(self.service.calls.details('event-1')['comments'], [])

    async def test_two_users_can_comment_without_mixing_replies(self):
        setting = await self.configure()
        await self.send()
        for user in (7, 8):
            await self.callback(setting, 'comment:event-1', user=user)
        for user in (8, 7):
            flow = self.service.flows.pending(setting['bot_id'], user, 'comment', 'event-1')
            await self.service._handle_update(setting, bot_token(), {'message': {
                'message_id': 600+user, 'chat': {'id': -1001, 'type': 'supergroup'}, 'message_thread_id': 42,
                'from': {'id': user}, 'text': f'Комментарий {user}', 'reply_to_message': {'message_id': flow['prompt_id']}}})
        comments = self.service.calls.details('event-1')['comments']
        self.assertEqual({(row['author_user_id'], row['body']) for row in comments},
                         {('7', 'Комментарий 7'), ('8', 'Комментарий 8')})

    async def test_reopening_comment_replaces_abandoned_prompt(self):
        setting = await self.configure()
        await self.send()
        await self.callback(setting, 'comment:event-1')
        first = self.service.flows.pending(setting['bot_id'], 7, 'comment', 'event-1')
        await self.callback(setting, 'comment:event-1')
        second = self.service.flows.pending(setting['bot_id'], 7, 'comment', 'event-1')
        self.assertNotEqual(first['id'], second['id'])
        self.assertIsNone(self.service.flows.get(first['id']))
        self.assertEqual(self.service.flows.cleanup_due()[0]['message_id'], first['prompt_id'])

    async def test_expired_comment_prompt_is_cleaned_up(self):
        setting = await self.configure()
        await self.send()
        await self.callback(setting, 'comment:event-1')
        with self.service.store.connect() as connection:
            connection.execute("UPDATE telegram_flows SET expires_at=0 WHERE kind='comment'")
        due = self.service.flows.cleanup_due()
        self.assertEqual(len(due), 1)
        self.assertNotEqual(due[0]['message_id'], self.service.store.delivery('event-1')['message_id'])

    async def test_cleanup_runs_separately_and_preserves_comment(self):
        setting = await self.configure()
        await self.send()
        self.service.calls.comment('event-1', '7', 'Иван Петров', 'Сохранено', 'cleanup-test')
        self.service.flows.delete_later(setting['bot_id'], -1001, 999)
        deleted = asyncio.Event()
        original = self.api.call
        async def capture(token, method, **payload):
            result = await original(token, method, **payload)
            if method == 'deleteMessage':
                deleted.set()
            return result
        self.api.call = capture
        self.service.running = True
        self.service.tasks = [asyncio.create_task(self.service._cleanup())]
        try:
            await asyncio.wait_for(deleted.wait(), timeout=2)
            self.assertEqual(len(self.service.calls.details('event-1')['comments']), 1)
            self.assertEqual(self.service.flows.cleanup_due(), [])
        finally:
            await self.service.stop()

    async def test_profile_registers_automatically_in_private_and_across_bots(self):
        setting = await self.configure()
        await self.send()
        for index, text in enumerate(['/sync', 'I.Petrov', 'Иван Петров']):
            await self.service._process_updates(setting, bot_token(), [{'update_id': index + 1, 'message': {
                'message_id': 200 + index, 'chat': {'id': 7, 'type': 'private'}, 'from': {'id': 7}, 'text': text}}], defer_setup=True)
        flow = self.service.flows.pending(setting['bot_id'], 7, 'profile')
        self.assertFalse(self.service.employees.by_user(7)['profile_complete'])
        await self.callback(setting, 'profile:' + flow['id'], flow=flow)
        profile = self.service.employees.by_user(7)
        self.assertEqual(profile['login'], 'i.petrov')
        self.assertFalse(profile['blocked'])
        other = await self.configure('msk', 222222, -2002, 85)
        self.assertEqual(self.service.interactions.identity(other, {'id': 7}), 'Иван Петров · i.petrov')

    async def private_message(self, setting, text):
        await self.service._handle_update(setting, bot_token(), {'message': {
            'message_id': 700, 'chat': {'id': 7, 'type': 'private'},
            'from': {'id': 7, 'first_name': 'Test'}, 'text': text}})

    async def test_identity_prefers_corporate_login_and_keeps_telegram(self):
        self.event()
        self.service.employees.observe({'id': 7, 'first_name': 'Telegram Name', 'username': 'test_user'})
        self.service.calls.change('event-1', 'assign', '7', 'Telegram Name')
        item = self.service.calls.get('event-1')
        self.assertEqual(item['assignee_short'], '@test_user')
        self.assertEqual(item['assignee_details'], 'Telegram: @test_user')
        self.service.employees.save('7', 'corp.login', 'Test Employee')
        item = self.service.calls.get('event-1')
        self.assertEqual(item['assignee_short'], 'corp.login')
        self.assertEqual(item['assignee_details'], 'Test Employee · corp.login · Telegram: @test_user')
        self.service.calls.change('event-1', 'release', '7', 'Test Employee')
        item = self.service.calls.get('event-1')
        self.assertIsNone(item['assignee_short'])
        self.assertIsNone(item['assignee_details'])

    async def test_identity_without_username_or_profile_has_safe_fallback(self):
        self.event()
        self.service.calls.change('event-1', 'assign', '7', 'Old Name')
        self.assertEqual(self.service.calls.get('event-1')['assignee_short'], 'Telegram ID 7')
        self.service.employees.observe({'id': 7, 'first_name': 'Telegram Name'})
        self.assertEqual(self.service.calls.get('event-1')['assignee_details'], 'Telegram: Telegram Name')

    async def test_callback_spam_is_bounded_and_other_employee_can_act(self):
        setting = await self.configure()
        await self.send()
        for index in range(200):
            query = self.query()
            query['id'] = f'spam-{index}'
            query['data'] = ('claim:' if index % 2 == 0 else 'release:') + 'event-1'
            await self.service._callback(setting, bot_token(), query)
        item = self.service.calls.details('event-1')
        self.assertEqual(len(item['actions']), 12)
        answers = [data for _, method, data in self.api.calls if method == 'answerCallbackQuery']
        self.assertEqual(len(answers), 13)
        await self.callback(setting, 'claim:event-1', user=8)
        self.assertEqual(self.service.calls.get('event-1')['acknowledged_user_id'], '8')

    async def test_private_message_spam_does_not_flood_replies(self):
        setting = await self.configure()
        for _ in range(100):
            await self.private_message(setting, '/start')
        self.assertEqual(sum(method == 'sendMessage' for _, method, _ in self.api.calls), 12)

    async def test_parallel_assignments_keep_single_current_assignee(self):
        self.event()
        await asyncio.gather(*(asyncio.to_thread(self.service.calls.change,
            'event-1', 'assign', str(user), f'Employee {user}', receipt=f'parallel-{user}') for user in range(1, 17)))
        item = self.service.calls.details('event-1')
        self.assertEqual(item['revision'], 16)
        self.assertEqual(len(item['actions']), 16)
        self.assertIn(item['acknowledged_user_id'], {str(user) for user in range(1, 17)})

    async def test_private_menu_and_cancel_registration(self):
        setting = await self.configure()
        await self.private_message(setting, '/start')
        payload = [data for _, method, data in self.api.calls if method == 'sendMessage'][-1]
        self.assertTrue(payload['reply_markup']['is_persistent'])
        self.assertIn(self.service.profile_ui.FILL, str(payload['reply_markup']))
        self.assertNotIn(self.service.profile_ui.CANCEL, str(payload['reply_markup']))
        self.assertIsNone(self.service.flows.pending(setting['bot_id'], 7, 'profile'))
        await self.private_message(setting, self.service.profile_ui.FILL)
        self.assertEqual(self.service.flows.pending(setting['bot_id'], 7, 'profile')['step'], 'login')
        self.assertIn(self.service.profile_ui.CANCEL, str(self.api.calls[-1][2]['reply_markup']))
        await self.private_message(setting, 'invalid login!')
        self.assertEqual(self.service.flows.pending(setting['bot_id'], 7, 'profile')['step'], 'login')
        await self.private_message(setting, self.service.profile_ui.CANCEL)
        self.assertIsNone(self.service.flows.pending(setting['bot_id'], 7, 'profile'))
        self.assertNotIn(self.service.profile_ui.CANCEL, str(self.api.calls[-1][2]['reply_markup']))

    async def test_private_cancel_hidden_after_save_and_in_other_bot(self):
        setting = await self.configure()
        for text in ('/sync', 'test.login', 'Test Employee'):
            await self.private_message(setting,text)
        profile = self.service.employees.by_user(7)
        self.assertNotIn(self.service.profile_ui.CANCEL,
            str(self.service.profile_ui.keyboard(profile,bot_token(222222))))
        flow = self.service.flows.pending(setting['bot_id'],7,'profile')
        await self.callback(setting,'profile:'+flow['id'],flow=flow)
        self.assertNotIn(self.service.profile_ui.CANCEL,str(self.api.calls[-2][2]['reply_markup']))
        self.assertNotIn(self.service.profile_ui.CANCEL,
            str(self.service.profile_ui.keyboard(self.service.employees.by_user(7),bot_token())))

    async def test_duplicate_login_confirmation_can_restart(self):
        setting = await self.configure()
        self.service.employees.save('8', 'taken.login', 'Other Employee')
        for text in ('/sync', 'taken.login', 'Test Employee'):
            await self.private_message(setting, text)
        flow = self.service.flows.pending(setting['bot_id'], 7, 'profile')
        await self.callback(setting, 'profile:' + flow['id'], flow=flow)
        self.assertIsNone(self.service.flows.get(flow['id']))
        self.assertEqual(self.service.flows.pending(setting['bot_id'], 7, 'profile')['step'], 'login')
        self.assertFalse(self.service.employees.by_user(7)['profile_complete'])

    async def test_lock_prevents_self_edits_but_not_admin_updates(self):
        profile = self.service.employees.save('7', 'test.login', 'Test Employee')
        locked = self.service.employees.update(profile['id'], profile['login'], profile['full_name'],
            False, None, '7', 'admin', profile_locked=True)
        with self.assertRaises(ValueError):
            self.service.employees.save('7', 'changed.login', 'Other Employee')
        with self.assertRaises(ValueError):
            self.service.employees.clear_self('7', profile['id'], locked['updated_at'])
        updated = self.service.employees.update(profile['id'], 'admin.changed', 'Other Employee',
            False, None, '7', 'admin')
        self.assertTrue(updated['profile_locked'])
        self.assertEqual(updated['login'], 'admin.changed')

    async def test_lock_during_registration_rejects_old_confirmation(self):
        setting = await self.configure()
        for text in ('/sync', 'test.login', 'Test Employee'):
            await self.private_message(setting, text)
        flow = self.service.flows.pending(setting['bot_id'], 7, 'profile')
        profile = self.service.employees.by_user(7)
        self.service.employees.update(profile['id'], '', profile['full_name'], False, None, '7', 'admin', True)
        await self.callback(setting, 'profile:' + flow['id'], flow=flow)
        self.assertFalse(self.service.employees.by_user(7)['profile_complete'])
        self.assertIsNone(self.service.flows.pending(setting['bot_id'], 7, 'profile'))
        await self.private_message(setting, '/sync')
        self.assertIsNone(self.service.flows.pending(setting['bot_id'], 7, 'profile'))

    async def test_private_delete_preserves_identity_restrictions_and_history(self):
        setting = await self.configure()
        await self.send()
        profile = self.service.employees.save('7', 'test.login', 'Test Employee')
        self.service.employees.update(profile['id'], profile['login'], profile['full_name'], False, ['spb'], '7', 'admin')
        self.service.calls.comment('event-1', '7', 'Test Employee', 'Saved comment', 'delete-test')
        await self.private_message(setting, self.service.profile_ui.DELETE)
        flow = self.service.flows.pending(setting['bot_id'], 7, 'profile_delete')
        await self.callback(setting, 'pdelete:' + flow['id'], user=8, flow=flow)
        self.assertTrue(self.service.employees.by_user(7)['profile_complete'])
        await self.callback(setting, 'pdelete:' + flow['id'], flow=flow)
        result = self.service.employees.by_user(7)
        self.assertEqual(result['id'], profile['id'])
        self.assertEqual(result['allowed_areas'], ['spb'])
        self.assertFalse(result['profile_complete'])
        self.assertEqual(result['login'], '')
        self.assertEqual(len(self.service.calls.details('event-1')['comments']), 1)

    async def test_delete_confirmation_rejects_admin_change_or_lock(self):
        setting = await self.configure()
        profile = self.service.employees.save('7', 'test.login', 'Test Employee')
        await self.private_message(setting, self.service.profile_ui.DELETE)
        flow = self.service.flows.pending(setting['bot_id'], 7, 'profile_delete')
        updated = self.service.employees.update(profile['id'], 'admin.changed', 'Other Employee', False, None, '7', 'admin')
        self.assertNotEqual(profile['updated_at'], updated['updated_at'])
        await self.callback(setting, 'pdelete:' + flow['id'], flow=flow)
        self.assertEqual(self.service.employees.by_user(7)['login'], 'admin.changed')

    async def test_blocked_profile_cannot_self_unblock_or_act(self):
        setting = await self.configure()
        await self.send()
        profile = self.service.employees.save('7', 'i.petrov', 'Иван Петров')
        self.service.employees.update(profile['id'], profile['login'], profile['full_name'], True, None, '7', 'admin')
        with self.assertRaises(ValueError):
            self.service.employees.save('7', 'new.login', 'Иван Петров')
        await self.callback(setting, 'claim:event-1')
        self.assertEqual(self.service.calls.get('event-1')['call_state'], 'waiting')

    async def test_unique_login_and_region_restrictions(self):
        setting = await self.configure()
        profile = self.service.employees.save('7', 'i.petrov', 'Иван Петров')
        with self.assertRaises(ValueError):
            self.service.employees.save('8', 'I.PETROV', 'Иван Сидоров')
        self.service.employees.update(profile['id'], profile['login'], profile['full_name'], False, ['msk'], '7', 'admin')
        with self.assertRaises(ValueError):
            self.service.interactions.identity(setting, {'id': 7})

    async def test_require_profile_and_reassignment_policy(self):
        await self.configure()
        await self.send()
        self.service.store.set_policy('spb', True, False)
        setting = self.service._setting('spb')
        await self.callback(setting, 'claim:event-1')
        self.assertEqual(self.service.calls.get('event-1')['call_state'], 'waiting')
        self.service.employees.save('7', 'i.petrov', 'Иван Петров')
        self.service.employees.save('8', 'i.sidorov', 'Иван Сидоров')
        await self.callback(setting, 'claim:event-1')
        await self.callback(setting, 'release:event-1:1', user=8)
        self.assertIsNone(self.service.flows.pending(setting['bot_id'], 8, 'confirm', 'event-1'))

    async def test_outbox_revision_does_not_lose_changes_during_edit(self):
        await self.configure()
        await self.send()
        self.service.calls.comment('event-1', '7', 'Иван Петров', 'Первый', '1')
        event = self.service.store.pending_edits()[0]
        original = self.api.call
        async def call(token, method, **payload):
            if method == 'editMessageText':
                self.service.calls.comment('event-1', '7', 'Иван Петров', 'Второй', '2')
            return await original(token, method, **payload)
        self.api.call = call
        await self.service._edit(event)
        self.assertEqual(len(self.service.store.pending_edits()), 1)

    async def test_history_search_matches_comment_employee_and_state(self):
        await self.configure()
        await self.send()
        self.service.calls.comment('event-1', '7', 'Иван Петров', 'Заменили ПРОЕКТОР', '1')
        self.assertEqual(self.history.page(1, 10, query='проектор')['total'], 1)
        self.service.employees.save('7', 'i.petrov', 'Иван Петров')
        self.service.calls.change('event-1', 'claim', '7', 'Иван Петров')
        self.assertEqual(self.history.page(1, 10, query='I.PETROV', state='accepted')['total'], 1)
        self.assertEqual(self.history.page(1, 10, state='waiting')['total'], 0)

    def test_display_date_and_invalid_input(self):
        self.assertEqual(display_date('2026-10-20T14:32:08'), '20.10.2026 14:32:08')
        self.assertEqual(display_date(None), '')

    async def test_atomic_version_protects_simultaneous_changes(self):
        self.event()
        self.service.calls.change('event-1', 'claim', '7', 'Иван Петров')
        self.service.calls.change('event-1', 'takeover', '8', 'Иван Сидоров', 1)
        with self.assertRaises(StaleCall):
            self.service.calls.change('event-1', 'release', '7', 'Иван Петров', 1)

    def test_batch_state_uses_bounded_database_connections(self):
        with self.history._connect() as connection:
            connection.executemany('''INSERT INTO press_history(id,device_id,device_name,pressed_at,pressed_at_display)
                VALUES(?,?,?,?,?)''', [(f'batch-{index}',f'button-{index}','Кнопка','2026-10-20T12:00:00','20.10.2026 12:00:00') for index in range(1001)])
        ids = [f'batch-{index}' for index in range(1001)]
        with patch.object(self.history, '_connect', wraps=self.history._connect) as connect:
            items = self.service.calls.get_many(ids + ['missing', ids[0]])
        self.assertEqual(connect.call_count, 1)
        self.assertEqual([item['id'] for item in items], ids)

    def test_batch_state_resolves_current_profiles_once(self):
        self.event()
        self.service.employees.save('7', 'i.petrov', 'Иван Петров')
        self.service.calls.change('event-1','claim','7','Старое имя')
        with patch.object(self.service.employees, 'connect', wraps=self.service.employees.connect) as connect:
            items = self.service.calls.get_many(['event-1'])
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(items[0]['assignee_name'], 'Иван Петров · i.petrov')

    def test_comment_details_can_exclude_assignment_journal(self):
        self.event()
        self.service.calls.change('event-1','claim','7','Иван Петров')
        self.service.calls.comment('event-1','7','Иван Петров','Всё работает','web-comments')
        item = self.service.calls.details('event-1', include_actions=False)
        self.assertNotIn('actions', item)
        self.assertEqual(item['comments'][0]['body'], 'Всё работает')
        self.assertEqual(len(self.service.calls.details('event-1')['actions']), 2)

    def test_current_assignee_and_platform_follow_reassignment_and_release(self):
        self.event()
        first = self.service.calls.change('event-1', 'assign', '7', 'Иван Петров')
        self.assertEqual(first['assignee_name'], 'Иван Петров')
        self.assertEqual(first['assignee_source_label'], 'Telegram')
        second = self.service.calls.change('event-1', 'assign', '8', 'Анна Иванова')
        self.assertEqual(second['assignee_name'], 'Анна Иванова')
        released = self.service.calls.change('event-1', 'release', '7', 'Иван Петров')
        self.assertEqual(released['call_state'], 'waiting')
        self.assertIsNone(released['assignee_name'])
        self.assertIsNone(released['assignee_source'])

    def test_future_platform_does_not_use_telegram_profile_with_same_id(self):
        self.event()
        self.service.employees.save('7', 'i.petrov', 'Иван Петров')
        self.history.claim(['event-1'],'2026-10-20T16:00:00','20.10.2026 16:00:00','Другой сотрудник','other_platform','7')
        item = self.service.calls.get_many(['event-1'])[0]
        self.assertEqual(item['call_state'], 'accepted')
        self.assertEqual(item['assignee_name'], 'Другой сотрудник')
        self.assertEqual(item['assignee_source_label'], 'other_platform')
        self.assertEqual(self.history.page(1, 10, state='accepted')['total'], 1)

    def test_legacy_web_silence_is_not_a_staff_assignment(self):
        self.event()
        self.history.acknowledge(['event-1'],'2026-10-20T16:00:00','20.10.2026 16:00:00')
        item = self.service.calls.get('event-1')
        self.assertEqual(item['call_state'], 'waiting')
        self.assertIsNone(item['assignee_source_label'])

    async def test_private_message_discovers_user_without_registration(self):
        setting = await self.configure()
        message = {'chat':{'id':91,'type':'private'},'from':{'id':91,'first_name':'Иван','username':'test_employee'},'text':'Привет'}
        await self.service._handle_update(setting, bot_token(), {'message':message})
        item = self.service.employees.by_user(91)
        self.assertFalse(item['profile_complete'])
        self.assertEqual(item['login'], '')
        self.assertEqual(item['display_name'], 'Иван')
        self.assertEqual(self.service.employees.page('@test_employee')['total'], 1)

    async def test_group_user_is_common_across_bots_and_seen_writes_are_throttled(self):
        setting = await self.configure()
        user = {'id':92,'first_name':'Анна','last_name':'Иванова'}
        message = {'chat':{'id':-1001,'type':'supergroup'},'from':user,'text':'Сообщение в группе'}
        with patch.object(self.service.employees, 'observe', wraps=self.service.employees.observe) as observe:
            for _ in range(10):
                await self.service._handle_update(setting, bot_token(), {'message':message})
            self.assertEqual(observe.call_count, 1)
        first = self.service.employees.by_user(92)
        other = await self.configure('msk',222222,-2002,85)
        await self.service._handle_update(other, bot_token(222222), {'message':message})
        second = self.service.employees.by_user(92)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(second['last_seen_area_id'], 'msk')

    async def test_callback_alone_discovers_user_and_records_acceptance_time(self):
        setting = await self.configure()
        await self.send()
        await self.callback(setting, 'claim:event-1', user=93)
        self.assertIsNotNone(self.service.employees.by_user(93))
        item = self.service.calls.get('event-1')
        self.assertRegex(item['acknowledged_at_display'], r'^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}$')

    def test_admin_can_create_employee_without_telegram_id(self):
        item = self.service.employees.create('I.Petrov','Иван Петров',None,'admin')
        self.assertEqual(item['login'], 'i.petrov')
        self.assertTrue(item['profile_complete'])
        self.assertIsNone(item['telegram_user_id'])
        self.assertEqual(self.service.employees.audit(item['id'])[0]['action'], 'admin_created')

    def test_observe_preserves_admin_profile_and_block(self):
        item = self.service.employees.create('i.petrov','Иван Петров','94','admin')
        self.service.employees.update(item['id'],item['login'],item['full_name'],True,['spb'],'94','admin')
        current = self.service.employees.observe({'id':94,'first_name':'Другое имя','username':'another_name'},'msk')
        self.assertEqual(current['full_name'], 'Иван Петров')
        self.assertEqual(current['login'], 'i.petrov')
        self.assertTrue(current['blocked'])
        self.assertEqual(current['allowed_areas'], ['spb'])
        self.assertEqual(current['telegram_username'], 'another_name')

    def test_pending_profile_can_be_blocked_and_completed_in_admin(self):
        pending = self.service.employees.observe({'id':95,'first_name':'Иван'})
        blocked = self.service.employees.update(pending['id'],'','Иван',True,None,'95','admin')
        self.assertFalse(blocked['profile_complete'])
        self.assertTrue(blocked['blocked'])
        complete = self.service.employees.update(pending['id'],'i.petrov','Иван Петров',False,None,'95','admin')
        self.assertEqual(complete['id'], pending['id'])
        self.assertTrue(complete['profile_complete'])

    def test_self_registration_promotes_discovered_user(self):
        pending = self.service.employees.observe({'id':96,'first_name':'Иван'})
        registered = self.service.employees.save('96','i.petrov','Иван Петров')
        self.assertEqual(pending['id'], registered['id'])
        self.assertTrue(registered['profile_complete'])

    def test_duplicate_admin_create_does_not_steal_observed_user(self):
        pending = self.service.employees.observe({'id':97,'first_name':'Иван'})
        with self.assertRaises(ValueError):
            self.service.employees.create('i.petrov','Иван Петров','97','admin')
        self.assertEqual(self.service.employees.by_user(97)['id'], pending['id'])

    def test_unlinked_pending_user_can_be_discovered_again(self):
        pending = self.service.employees.observe({'id':98,'first_name':'Иван'})
        self.service.employees.update(pending['id'],'','Иван',False,None,None,'admin')
        rediscovered = self.service.employees.observe({'id':98,'first_name':'Иван'})
        self.assertNotEqual(pending['id'], rediscovered['id'])

    async def test_discovery_does_not_bypass_required_profile(self):
        await self.configure()
        self.service.store.set_policy('spb', True, True)
        setting = self.service._setting('spb')
        self.service.employees.observe({'id':99,'first_name':'Иван'})
        with self.assertRaisesRegex(ValueError, 'заполните профиль'):
            self.service.interactions.identity(setting, {'id':99})

    def test_existing_telegram_actors_can_be_imported_from_local_history(self):
        self.event()
        self.service.calls.change('event-1','claim','100','Иван Петров')
        with self.history._connect() as connection:
            connection.execute("DELETE FROM runtime_state WHERE key='employee_discovery_backfill_v1'")
        self.service.employees.import_interactions(self.history)
        self.assertFalse(self.service.employees.by_user(100)['profile_complete'])
        self.assertEqual(self.service.employees.by_user(100)['display_name'], 'Иван Петров')
