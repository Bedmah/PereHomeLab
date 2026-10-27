import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from app.telegram_service import TelegramError, TelegramService
from tests import test_telegram as fixtures


class PrivateNotificationsTest(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.TelegramTestCase.setUp
    tearDown = fixtures.TelegramTestCase.tearDown
    configure = fixtures.TelegramTestCase.configure
    event = fixtures.TelegramTestCase.event
    send = fixtures.TelegramTestCase.send
    query = fixtures.TelegramTestCase.query

    async def message(self, setting, user_id, text, reply=None):
        self.message_counter = getattr(self,'message_counter',5000)+1
        message = {'message_id':self.message_counter,'chat':{'id':user_id,'type':'private'},
                   'from':{'id':user_id,'first_name':'Test Person'},'text':text}
        if reply is not None:
            message['reply_to_message']={'message_id':reply}
        await self.service._handle_update(setting,fixtures.bot_token(setting['bot_id']),{'message':message})
        return message

    async def subscriber(self, setting, user_id=7):
        employee = self.service.employees.observe({'id':user_id,'first_name':'Test Person'},setting['area_id'])
        self.service.employees.update(employee['id'],'employee.'+str(user_id),'Test Person',False,None,str(user_id),'admin',True)
        await self.message(setting,user_id,'/start')
        self.service.private.set_subscription(setting,user_id,True)
        return self.service.employees.get(employee['id'])

    def callback(self, setting, user_id=7, action='claim', event_id='event-1', message_id=None):
        delivery = self.service.private.delivery(event_id,setting['bot_id'],user_id)
        return {'id':'private-'+str(len(self.api.calls)),'from':{'id':user_id,'first_name':'Test Person'},
                'data':action+':'+event_id,'message':{'message_id':message_id or delivery['message_id'],
                    'chat':{'id':user_id,'type':'private'}}}

    async def test_subscription_menu_requires_confirmation_and_known_private_chat(self):
        setting = await self.configure()
        employee = self.service.employees.observe({'id':7,'first_name':'Test Person'},'spb')
        with self.assertRaises(ValueError):
            self.service.private.set_subscription(setting,7,True)
        await self.message(setting,7,'/subscribe')
        self.assertFalse(self.service.private.state(setting,7)['enabled'])
        self.service.employees.update(employee['id'],'employee.seven','Test Person',False,None,'7','admin',True)
        await self.message(setting,7,self.service.private.SUBSCRIBE)
        self.assertTrue(self.service.private.state(setting,7)['enabled'])
        self.assertIn(self.service.private.UNSUBSCRIBE,str(self.api.calls[-1][2]['reply_markup']))
        await self.message(setting,7,self.service.private.UNSUBSCRIBE)
        self.assertFalse(self.service.private.state(setting,7)['enabled'])

    async def test_no_history_replay_no_duplicates_and_restart_persistence(self):
        setting = await self.configure()
        self.event('old',when=datetime.now()-timedelta(seconds=2))
        await self.subscriber(setting)
        self.event('new'); await self.service.private.tick()
        self.assertIsNone(self.service.private.delivery('old',setting['bot_id'],7))
        self.assertEqual(self.service.private.delivery('new',setting['bot_id'],7)['state'],'sent')
        before = len(self.api.calls)
        await self.service.private.tick()
        self.assertEqual(len(self.api.calls),before)
        reopened = TelegramService(self.config,self.history,self.cities,self.service.logger,AsyncMock(),self.api)
        self.assertTrue(reopened.private.state(setting,7)['enabled'])
        await reopened.private.tick()
        self.assertEqual(len(self.api.calls),before)

    async def test_group_and_private_assignments_and_cancellation_synchronize(self):
        setting = await self.configure()
        await self.subscriber(setting,7); await self.subscriber(setting,8)
        await self.send(); await self.service.private.tick()
        token = fixtures.bot_token()
        await self.service._callback(setting,token,self.query(user_id=9))
        self.assertEqual(self.service.calls.get('event-1')['acknowledged_user_id'],'9')
        await self.service.private.tick()
        await self.service._callback(setting,token,self.callback(setting,8))
        self.assertEqual(self.service.calls.get('event-1')['acknowledged_user_id'],'8')
        await self.service._edit(self.service.store.delivery('event-1'))
        await self.service.private.tick()
        revision = self.service.calls.get('event-1')['revision']
        for user_id in (7,8):
            self.assertEqual(self.service.private.delivery('event-1',setting['bot_id'],user_id)['synced_revision'],revision)
        await self.service._callback(setting,token,self.callback(setting,7,'release'))
        self.assertIsNone(self.service.calls.get('event-1')['acknowledged_user_id'])

    async def test_subscription_watermark_survives_history_maintenance(self):
        setting=await self.configure()
        self.event('old',when=datetime.now()-timedelta(seconds=2))
        await self.subscriber(setting)
        with self.history._connect() as connection:
            connection.execute('DELETE FROM press_history WHERE id=?',('old',))
        with self.history._connect() as connection:
            connection.execute('VACUUM')
        self.event('after-maintenance'); await self.service.private.tick()
        self.assertEqual(self.service.private.delivery('after-maintenance',setting['bot_id'],7)['state'],'sent')

    async def test_revocation_is_sticky_and_cancels_old_queue(self):
        setting = await self.configure()
        employee = await self.subscriber(setting)
        self.event(); self.service.private.enqueue()
        self.service.employees.update(employee['id'],'employee.7','Test Person',False,None,'7','admin',False)
        self.assertFalse(self.service.private.state(setting,7)['enabled'])
        self.assertEqual(self.service.private.delivery('event-1',setting['bot_id'],7)['state'],'canceled')
        self.service.employees.update(employee['id'],'employee.7','Test Person',False,None,'7','admin',True)
        self.assertFalse(self.service.private.state(setting,7)['enabled'])
        self.service.private.set_subscription(setting,7,True)
        before = len(self.api.calls); await self.service.private.tick()
        self.assertEqual(len(self.api.calls),before)

    async def test_private_acceptance_before_group_delivery_still_posts_group_card(self):
        setting = await self.configure()
        await self.subscriber(setting)
        self.service.enqueue(self.event())
        await self.service.private.tick()
        await self.service._callback(setting,fixtures.bot_token(),self.callback(setting))
        queued = self.service.store.due()
        self.assertIsNotNone(queued)
        await self.service._send(queued)
        self.assertEqual(self.service.store.delivery('event-1')['state'],'sent')
        group = [payload for _,method,payload in self.api.calls if method=='sendMessage' and payload.get('chat_id')==setting['chat_id']]
        self.assertIn('Вызов принят',group[-1]['text'])

    async def test_unsubscribe_and_location_block_do_not_cancel_assignment(self):
        setting = await self.configure()
        employee = await self.subscriber(setting)
        self.event(); await self.service.private.tick()
        await self.service._callback(setting,fixtures.bot_token(),self.callback(setting))
        self.service.private.set_subscription(setting,7,False)
        self.assertEqual(self.service.calls.get('event-1')['acknowledged_user_id'],'7')
        self.service.private.set_subscription(setting,7,True)
        self.service.employees.set_area_access(employee['id'],'spb',False,'admin')
        self.assertFalse(self.service.private.state(setting,7)['enabled'])
        await self.service._callback(setting,fixtures.bot_token(),self.callback(setting,action='release'))
        self.assertEqual(self.service.calls.get('event-1')['acknowledged_user_id'],'7')

    async def test_forged_message_recipient_and_foreign_bot_are_rejected(self):
        setting = await self.configure()
        await self.subscriber(setting,7); await self.subscriber(setting,8)
        self.event(); await self.service.private.tick()
        own = self.service.private.delivery('event-1',setting['bot_id'],7)
        await self.service._callback(setting,fixtures.bot_token(),self.callback(setting,8,message_id=own['message_id']))
        self.assertIsNone(self.service.calls.get('event-1')['acknowledged_user_id'])
        other = await self.configure('msk',222222,-1002)
        await self.service._callback(other,fixtures.bot_token(222222),self.callback(setting))
        self.assertIsNone(self.service.calls.get('event-1')['acknowledged_user_id'])

    async def test_private_comments_accept_plain_text_and_sync_without_new_alert(self):
        setting = await self.configure()
        await self.subscriber(setting); await self.send(); await self.service.private.tick()
        await self.service._callback(setting,fixtures.bot_token(),self.callback(setting,action='comment'))
        flow = self.service.flows.comments_for_user(setting['bot_id'],7,7)[0]
        self.assertIsNone(flow['thread_id'])
        await self.message(setting,7,'Checked room')
        self.assertEqual(self.service.calls.details('event-1')['comments'][0]['body'],'Checked room')
        self.assertFalse(self.service.flows.comments_for_user(setting['bot_id'],7,7))
        self.assertTrue(self.service.flows.cleanup_due())
        before = len([call for call in self.api.calls if call[1]=='sendMessage'])
        await self.service.private.tick(); await self.service._edit(self.service.store.delivery('event-1'))
        self.assertEqual(len([call for call in self.api.calls if call[1]=='sendMessage']),before)

    async def test_comment_rechecks_confirmation_after_prompt(self):
        setting = await self.configure()
        employee = await self.subscriber(setting)
        self.event(); await self.service.private.tick()
        await self.service._callback(setting,fixtures.bot_token(),self.callback(setting,action='comment'))
        self.service.employees.update(employee['id'],'employee.7','Test Person',False,None,'7','admin',False)
        await self.message(setting,7,'Unverified comment')
        self.assertEqual(self.service.calls.details('event-1')['comments'],[])

    async def test_private_comment_can_reply_to_original_notification(self):
        setting=await self.configure()
        await self.subscriber(setting)
        self.event(); await self.service.private.tick()
        await self.service._callback(setting,fixtures.bot_token(),self.callback(setting,action='comment'))
        delivery=self.service.private.delivery('event-1',setting['bot_id'],7)
        await self.message(setting,7,'Original reply',reply=delivery['message_id'])
        self.assertEqual(self.service.calls.details('event-1')['comments'][0]['body'],'Original reply')

    async def test_delivery_batch_is_bounded(self):
        setting=await self.configure()
        for user_id in range(7,12):
            await self.subscriber(setting,user_id)
        self.event(); await self.service.private.tick()
        sent=[self.service.private.delivery('event-1',setting['bot_id'],user_id)['state'] for user_id in range(7,12)]
        self.assertEqual(sent.count('sent'),4)
        await self.service.private.tick()
        self.assertTrue(all(self.service.private.delivery('event-1',setting['bot_id'],user_id)['state']=='sent' for user_id in range(7,12)))

    async def test_rate_limit_does_not_stall_another_bot(self):
        import time
        setting=await self.configure()
        other=await self.configure('msk',222222,-1002)
        await self.subscriber(setting,7); await self.subscriber(other,8)
        self.event(); self.event('other','msk')
        self.service.private.backoff[setting['bot_id']]=time.time()+60
        await self.service.private.tick()
        self.assertEqual(self.service.private.delivery('other',other['bot_id'],8)['state'],'sent')
        self.assertEqual(self.service.private.delivery('event-1',setting['bot_id'],7)['state'],'queued')

    async def test_blocked_recipient_does_not_break_other_subscribers(self):
        setting = await self.configure()
        await self.subscriber(setting,7); await self.subscriber(setting,8)
        original = self.api.call
        async def fail_one(token,method,**payload):
            if method=='sendMessage' and payload.get('chat_id')==7:
                raise TelegramError('Forbidden',403)
            return await original(token,method,**payload)
        self.api.call=fail_one
        self.event(); await self.service.private.tick()
        self.assertFalse(self.service.private.state(setting,7)['enabled'])
        self.assertTrue(self.service.private.state(setting,7)['blocked'])
        self.assertEqual(self.service.private.delivery('event-1',setting['bot_id'],8)['state'],'sent')

    async def test_uncertain_send_is_not_repeated(self):
        setting = await self.configure()
        await self.subscriber(setting)
        self.event()
        self.api.failures['sendMessage']=[TelegramError('Timeout',retryable=True,uncertain=True)]
        await self.service.private.tick()
        self.assertEqual(self.service.private.delivery('event-1',setting['bot_id'],7)['state'],'uncertain')
        before=len(self.api.calls); await self.service.private.tick()
        self.assertEqual(len(self.api.calls),before)

    async def test_resubscribe_during_retry_does_not_replay_old_call(self):
        setting=await self.configure()
        await self.subscriber(setting)
        self.event()
        original=self.api.call
        async def resubscribe(token,method,**payload):
            if method=='sendMessage':
                self.service.private.set_subscription(setting,7,False)
                self.service.private.set_subscription(setting,7,True)
                raise TelegramError('Rate limited',429,retryable=True,retry_after=1)
            return await original(token,method,**payload)
        self.api.call=resubscribe
        await self.service.private.tick()
        self.api.call=original
        self.service.private.backoff.clear()
        with self.service.store.connect() as connection:
            connection.execute('UPDATE telegram_private_outbox SET next_attempt=0')
        before=len(self.api.calls)
        await self.service.private.tick()
        self.assertEqual(len(self.api.calls),before)
        self.assertEqual(self.service.private.delivery('event-1',setting['bot_id'],7)['state'],'canceled')

    async def test_revision_changed_during_send_is_synchronized(self):
        setting=await self.configure()
        await self.subscriber(setting)
        self.event()
        original=self.api.call
        async def changed_during_send(token,method,**payload):
            result=await original(token,method,**payload)
            if method=='sendMessage':
                self.service.calls.change('event-1','assign','7','Test Person',receipt='during-send')
            return result
        self.api.call=changed_during_send
        await self.service.private.tick()
        self.api.call=original
        await self.service.private.tick()
        delivery=self.service.private.delivery('event-1',setting['bot_id'],7)
        self.assertEqual(delivery['synced_revision'],self.service.calls.get('event-1')['revision'])
        self.assertEqual(len([call for call in self.api.calls if call[1]=='editMessageText']),1)

    async def test_expired_call_is_not_sent_and_paused_location_resumes(self):
        import time
        setting=await self.configure()
        await self.subscriber(setting)
        self.event(); self.service.private.enqueue()
        with self.service.store.connect() as connection:
            connection.execute('UPDATE telegram_private_outbox SET expires_at=?',(time.time()-1,))
        await self.service.private.tick()
        self.assertEqual(self.service.private.delivery('event-1',setting['bot_id'],7)['state'],'expired')
        self.event('paused',when=datetime.now()+timedelta(seconds=1))
        with self.service.store.connect() as connection:
            connection.execute('UPDATE telegram_regions SET enabled=0 WHERE area_id=?',('spb',))
        self.service.private.enqueue()
        self.assertIsNone(self.service.private.delivery('paused',setting['bot_id'],7))
        with self.service.store.connect() as connection:
            connection.execute('UPDATE telegram_regions SET enabled=1 WHERE area_id=?',('spb',))
        await self.service.private.tick()
        self.assertEqual(self.service.private.delivery('paused',setting['bot_id'],7)['state'],'sent')

    async def test_unconfirmed_group_action_keeps_existing_permissions(self):
        setting=await self.configure()
        await self.send()
        await self.service._callback(setting,fixtures.bot_token(),self.query(user_id=9))
        self.assertEqual(self.service.calls.get('event-1')['acknowledged_user_id'],'9')
        self.assertFalse(self.service.employees.by_user(9)['profile_locked'])
        with self.assertRaises(ValueError):
            self.service.private.set_subscription(setting,9,True)

    async def test_interrupted_send_is_not_resent_after_restart(self):
        setting=await self.configure()
        await self.subscriber(setting)
        self.event(); self.service.private.enqueue()
        delivery=self.service.private.delivery('event-1',setting['bot_id'],7)
        self.service.private.update(delivery,'sending')
        reopened=TelegramService(self.config,self.history,self.cities,self.service.logger,AsyncMock(),self.api)
        before=len(self.api.calls)
        await reopened.private.run()
        await reopened.private.tick()
        self.assertEqual(reopened.private.delivery('event-1',setting['bot_id'],7)['state'],'uncertain')
        self.assertEqual(len(self.api.calls),before)
