import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_crm_personal import PersonalHTTPTests, DSN
from personal_voice import extract, BoundedBuffer, MAX_BYTES
from personal_reminders import tick
from crm.personal import save_task

class VoiceParsingTests(unittest.TestCase):
    def test_separate_deadline_and_reminder(self):
        from zoneinfo import ZoneInfo
        now=datetime(2026,11,16,12,tzinfo=ZoneInfo('Europe/Moscow'))
        day,due,reminder=extract('Завтра в 10 утра подготовить план. Напомни сегодня в 17', 'Europe/Moscow',now)
        self.assertEqual((str(day),due.hour,reminder.hour,reminder.day),('2026-11-17',10,17,16))
        day,due,reminder=extract('Завтра подготовить план', 'Europe/Moscow',now)
        self.assertEqual(str(day),'2026-11-17'); self.assertIsNone(due)

    def test_morning_is_not_a_deadline_time_and_ambiguous_is_not_guessed(self):
        from zoneinfo import ZoneInfo
        now=datetime(2026,11,16,12,tzinfo=ZoneInfo('Europe/Moscow'))
        self.assertIsNone(extract('Завтра утром подготовить план', 'Europe/Moscow',now)[1])
        self.assertEqual(extract('Завтра в три дня подготовить план', 'Europe/Moscow',now)[1].hour,15)
        self.assertIsNone(extract('Завтра в двадцать один подготовить план', 'Europe/Moscow',now)[1])

    def test_buffer_rejects_actual_bytes(self):
        out=BoundedBuffer(); out.write(b'a'*MAX_BYTES)
        with self.assertRaises(ValueError): out.write(b'a')

@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class ReminderTests(PersonalHTTPTests):
    test_owner_isolation_versions_and_history = None
    test_parallel_updates_and_support_are_denied = None
    test_same_person_second_invite_has_same_profile_and_pilot_closed = None

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.pool.execute((Path(__file__).resolve().parents[1]/'migrations/046_crm_personal_telegram.sql').read_text(encoding='utf-8-sig'))
        await self.pool.execute((Path(__file__).resolve().parents[1]/'migrations/047_crm_personal_voice_reminders.sql').read_text(encoding='utf-8-sig'))
        await self.pool.execute('TRUNCATE crm_personal_voice_drafts, crm_personal_telegram_events, crm_personal_telegram_state')
        self.login(123)
        await self.request_json('PUT','/api/personal/profile',{'display_name':'Первый','timezone':'Europe/Moscow'})
        self.owner=await self.pool.fetchval('SELECT id FROM crm_personal_profiles WHERE telegram_user_id=123')

    async def test_reminder_sends_once_and_title_edit_does_not_resend(self):
        task=await save_task(self.pool,self.owner,{'title':'Тест','due_at':datetime(2020,1,1,tzinfo=timezone.utc)})
        bot=SimpleNamespace(send_message=AsyncMock())
        await tick(self.pool,bot); await tick(self.pool,bot)
        self.assertEqual(bot.send_message.await_count,1)
        await save_task(self.pool,self.owner,{'title':'Новое название'},task['id'],1)
        await tick(self.pool,bot)
        self.assertEqual(bot.send_message.await_count,1)

    async def test_unknown_delivery_not_retried_and_completed_not_sent(self):
        one=await save_task(self.pool,self.owner,{'title':'Неизвестно','due_at':datetime(2020,1,1,tzinfo=timezone.utc)})
        done=await save_task(self.pool,self.owner,{'title':'Готово','status':'done','due_at':datetime(2020,1,1,tzinfo=timezone.utc)})
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError()))
        await tick(self.pool,bot); await tick(self.pool,bot)
        self.assertEqual(bot.send_message.await_count,1)
        self.assertEqual(await self.pool.fetchval('SELECT status FROM crm_personal_reminder_delivery WHERE task_id=$1',one['id']),'uncertain')
        self.assertIsNone(await self.pool.fetchval('SELECT status FROM crm_personal_reminder_delivery WHERE task_id=$1',done['id']))

    async def test_voice_dispatch_mock_asr_preview_and_save(self):
        from aiogram import Bot, Dispatcher, Router
        from aiogram.types import Update
        from personal_voice import install
        bot=Bot('123456:test'); sent=[]
        async def fake_call(method, **kwargs): sent.append(method); return True
        async def fake_download(path, destination, **kwargs): destination.write(b'synthetic'); return destination
        router=Router(); install(router,lambda:self.pool)
        dp=Dispatcher(); dp.include_router(router)
        message={'update_id':500,'message':{'message_id':500,'date':1,'chat':{'id':123,'type':'private'},
            'from':{'id':123,'is_bot':False,'first_name':'Тест'},'voice':{'file_id':'fake','file_unique_id':'fake','duration':3,'file_size':9}}}
        with patch.object(Bot,'__call__',side_effect=fake_call), \
             patch.object(Bot,'get_file',new=AsyncMock(return_value=SimpleNamespace(file_path='test.ogg',file_size=9))), \
             patch.object(Bot,'download_file',side_effect=fake_download), \
             patch('personal_voice.wav_from_audio',new=AsyncMock(return_value=b'wav')), \
             patch('personal_voice.deepgram_client.transcribe_bytes',new=AsyncMock(return_value=('[00:00 Спикер 0] Завтра в 10 утра план. Напомни утром.',3))) as asr:
            await dp.feed_update(bot,Update.model_validate(message,context={'bot':bot}))
            await dp.feed_update(bot,Update.model_validate(message,context={'bot':bot}))
            self.assertEqual(asr.await_count,1)
            draft=await self.pool.fetchrow('SELECT * FROM crm_personal_voice_drafts WHERE telegram_user_id=123')
            self.assertEqual(draft['status'],'preview')
            self.assertNotIn('Спикер',draft['title'])
            self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_tasks'),0)
            cb={'update_id':501,'callback_query':{'id':'save-voice','chat_instance':'x','from':{'id':123,'is_bot':False,'first_name':'Тест'},'data':'pv:save:'+draft['token'],'message':{'message_id':10,'date':1,'chat':{'id':123,'type':'private'}}}}
            await dp.feed_update(bot,Update.model_validate(cb,context={'bot':bot}))
            await dp.feed_update(bot,Update.model_validate(cb,context={'bot':bot}))
            _, result=await self.request_json('GET','/api/personal/tasks')
            self.assertEqual(len(result['tasks']),1)
            self.assertIn('Завтра',result['tasks'][0]['title'])

    async def test_override_disabled_revoked_and_rescheduled(self):
        now=datetime.now(timezone.utc)
        future=await save_task(self.pool,self.owner,{'title':'Будущее','due_at':now+timedelta(days=2)})
        disabled=await save_task(self.pool,self.owner,{'title':'Выключено','due_at':now-timedelta(days=2),'reminders_enabled':False})
        override=await save_task(self.pool,self.owner,{'title':'Явное','due_at':now+timedelta(days=2),'reminder_at':now-timedelta(minutes=2)})
        bot=SimpleNamespace(send_message=AsyncMock())
        await tick(self.pool,bot)
        self.assertEqual(bot.send_message.await_count,1)
        self.assertEqual(await self.pool.fetchval('SELECT status FROM crm_personal_reminder_delivery WHERE task_id=$1',override['id']),'sent')
        await save_task(self.pool,self.owner,{'due_at':(now-timedelta(days=1))},future['id'],1)
        await self.pool.execute('UPDATE crm_invites SET revoked_at=now() WHERE telegram_user_id=123')
        await tick(self.pool,bot)
        self.assertEqual(bot.send_message.await_count,1)
        self.assertEqual(await self.pool.fetchval('SELECT status FROM crm_personal_reminder_delivery WHERE task_id=$1',future['id']),'blocked')
        self.assertIsNone(await self.pool.fetchval('SELECT status FROM crm_personal_reminder_delivery WHERE task_id=$1',disabled['id']))

    async def test_retry_after_is_never_clamped_and_uncertain_recovered(self):
        from aiogram.exceptions import TelegramRetryAfter
        from aiogram.methods import SendMessage
        task=await save_task(self.pool,self.owner,{'title':'Лимит','due_at':datetime(2020,1,1,tzinfo=timezone.utc)})
        error=TelegramRetryAfter(method=SendMessage(chat_id=123,text='x'),message='rate limit',retry_after=7200)
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=error))
        await tick(self.pool,bot)
        state=await self.pool.fetchrow('SELECT *,extract(epoch from retry_at-now()) AS remaining FROM crm_personal_reminder_delivery WHERE task_id=$1',task['id'])
        self.assertEqual(state['status'],'pending'); self.assertGreater(state['remaining'],7100)
        await self.pool.execute("UPDATE crm_personal_reminder_delivery SET status='sending',updated_at=now()-interval '6 minutes' WHERE task_id=$1",task['id'])
        await tick(self.pool,bot)
        self.assertEqual(await self.pool.fetchval('SELECT status FROM crm_personal_reminder_delivery WHERE task_id=$1',task['id']),'uncertain')
        self.assertEqual(bot.send_message.await_count,1)

    async def test_concurrent_ticks_send_once(self):
        import asyncio
        await save_task(self.pool,self.owner,{'title':'Параллельно','due_at':datetime(2020,1,1,tzinfo=timezone.utc)})
        async def slow(*args,**kwargs): await asyncio.sleep(.05)
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=slow))
        await asyncio.gather(tick(self.pool,bot),tick(self.pool,bot))
        self.assertEqual(bot.send_message.await_count,1)

    async def test_voice_fail_closed_for_nonpilot_group_and_oversize(self):
        from aiogram import Bot, Dispatcher, Router
        from aiogram.types import Update
        from personal_voice import install
        bot=Bot('123456:test'); router=Router(); install(router,lambda:self.pool)
        dp=Dispatcher(); dp.include_router(router)
        def payload(tg,chat,kind,size):
            return {'update_id':tg+size,'message':{'message_id':tg+size,'date':1,'chat':{'id':chat,'type':kind},'from':{'id':tg,'is_bot':False,'first_name':'Тест'},'voice':{'file_id':'f','file_unique_id':'u','duration':3,'file_size':size}}}
        with patch.object(Bot,'__call__',new=AsyncMock(return_value=True)), \
             patch.object(Bot,'get_file',new=AsyncMock()) as download, \
             patch('personal_voice.deepgram_client.transcribe_bytes',new=AsyncMock()) as asr:
            for p in (payload(125,125,'private',9),payload(123,-10,'group',9),payload(123,123,'private',MAX_BYTES+1)):
                await dp.feed_update(bot,Update.model_validate(p,context={'bot':bot}))
            download.assert_not_awaited(); asr.assert_not_awaited()
            self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_voice_drafts'),0)

    async def test_failed_asr_allows_new_voice_and_old_callback_is_rejected(self):
        from aiogram import Bot, Dispatcher, Router
        from aiogram.types import Update
        from personal_voice import install
        bot=Bot('123456:test'); router=Router(); install(router,lambda:self.pool)
        dp=Dispatcher(); dp.include_router(router)
        def voice(msg_id):
            return {'update_id':msg_id,'message':{'message_id':msg_id,'date':1,'chat':{'id':123,'type':'private'},'from':{'id':123,'is_bot':False,'first_name':'Тест'},'voice':{'file_id':'f','file_unique_id':'u','duration':3,'file_size':9}}}
        async def download(path,destination,**kwargs): destination.write(b'a'); return destination
        with patch.object(Bot,'__call__',new=AsyncMock(return_value=True)), \
             patch.object(Bot,'get_file',new=AsyncMock(return_value=SimpleNamespace(file_path='x',file_size=1))), \
             patch.object(Bot,'download_file',side_effect=download), \
             patch('personal_voice.wav_from_audio',new=AsyncMock(return_value=b'wav')), \
             patch('personal_voice.deepgram_client.transcribe_bytes',new=AsyncMock(side_effect=[RuntimeError('private'),('Новая задача',2)])):
            await dp.feed_update(bot,Update.model_validate(voice(701),context={'bot':bot}))
            await dp.feed_update(bot,Update.model_validate(voice(702),context={'bot':bot}))
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM crm_personal_voice_drafts WHERE status='failed'"),1)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM crm_personal_voice_drafts WHERE status='preview'"),1)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_tasks'),0)

    async def test_schedule_mode_change_increments_revision_without_title_or_due_changes_under_override(self):
        due=datetime(2030,1,1,14,tzinfo=timezone.utc)
        task=await save_task(self.pool,self.owner,{'title':'Срок','due_at':due})
        task=await save_task(self.pool,self.owner,{'reminder_at':due},task['id'],task['version'])
        self.assertEqual(task['reminder_revision'],2)
        task=await save_task(self.pool,self.owner,{'title':'Новое имя'},task['id'],task['version'])
        task=await save_task(self.pool,self.owner,{'due_at':due+timedelta(hours=1)},task['id'],task['version'])
        self.assertEqual(task['reminder_revision'],2)
        task=await save_task(self.pool,self.owner,{'reminder_at':None},task['id'],task['version'])
        self.assertEqual(task['reminder_revision'],3)

    async def test_real_demo_router_menu_cancels_voice_title_edit(self):
        from aiogram import Bot, Dispatcher
        from aiogram.types import Update
        import demo_bot
        bot=Bot('123456:test'); dp=Dispatcher(); dp.include_router(demo_bot.router)
        self.addCleanup(setattr, demo_bot.router, '_parent_router', None)
        await self.pool.execute("INSERT INTO crm_personal_voice_drafts(event_key,telegram_user_id,token,status,transcript,title) VALUES('real-router',123,'0123456789abcdef','preview','Старая','Старая')")
        await self.pool.execute("INSERT INTO crm_personal_telegram_state(telegram_user_id,action,token) VALUES(123,'voice_edit_title','0123456789abcdef') ON CONFLICT(telegram_user_id) DO UPDATE SET action=excluded.action,token=excluded.token")
        event={'update_id':801,'message':{'message_id':801,'date':1,'chat':{'id':123,'type':'private'},'from':{'id':123,'is_bot':False,'first_name':'Тест'},'text':'📋 Личные задачи'}}
        with patch.object(demo_bot,'PG_POOL',self.pool), patch.object(Bot,'__call__',new=AsyncMock(return_value=True)):
            await dp.feed_update(bot,Update.model_validate(event,context={'bot':bot}))
        self.assertEqual(await self.pool.fetchval("SELECT action FROM crm_personal_telegram_state WHERE telegram_user_id=123"),'idle')
        self.assertEqual(await self.pool.fetchval("SELECT title FROM crm_personal_voice_drafts WHERE event_key='real-router'"),'Старая')

    async def test_default_morning_sends_before_afternoon_deadline(self):
        from zoneinfo import ZoneInfo
        now=datetime.now(timezone.utc)
        zones=['Etc/GMT']+[f'Etc/GMT{sign}{n}' for sign in ('+','-') for n in range(1,13)]
        zone=next(z for z in zones if 10 <= now.astimezone(ZoneInfo(z)).hour <= 21)
        local=now.astimezone(ZoneInfo(zone))
        due=local.replace(hour=23,minute=59,second=0,microsecond=0)
        await self.pool.execute('UPDATE crm_personal_profiles SET timezone=$2 WHERE id=$1',self.owner,zone)
        await save_task(self.pool,self.owner,{'title':'Срок позже утра','due_at':due})
        bot=SimpleNamespace(send_message=AsyncMock())
        await tick(self.pool,bot)
        self.assertEqual(bot.send_message.await_count,1)
        self.assertIn('Срок задачи:',bot.send_message.await_args.args[1])

    async def test_deadline_before_nine_waits_until_exact_due(self):
        from zoneinfo import ZoneInfo
        now=datetime.now(timezone.utc)
        zones=['Etc/GMT']+[f'Etc/GMT{sign}{n}' for sign in ('+','-') for n in range(1,13)]
        zone=next(z for z in zones if 1 <= now.astimezone(ZoneInfo(z)).hour <= 7)
        await self.pool.execute('UPDATE crm_personal_profiles SET timezone=$2 WHERE id=$1',self.owner,zone)
        task=await save_task(self.pool,self.owner,{'title':'Ранний срок','due_at':now+timedelta(minutes=10)})
        bot=SimpleNamespace(send_message=AsyncMock())
        await tick(self.pool,bot)
        self.assertEqual(bot.send_message.await_count,0)
        await save_task(self.pool,self.owner,{'due_at':now-timedelta(minutes=10)},task['id'],task['version'])
        await tick(self.pool,bot)
        self.assertEqual(bot.send_message.await_count,1)
