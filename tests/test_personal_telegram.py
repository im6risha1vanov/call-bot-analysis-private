"""Personal Telegram integration through an isolated database and mocked Bot API."""
import os
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.test_crm_personal import PersonalHTTPTests, DSN
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Update
from personal_telegram import install


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class PersonalTelegramTests(PersonalHTTPTests):
    # HTTP behavior is tested by PersonalHTTPTests itself, without this fixture.
    test_owner_isolation_versions_and_history = None
    test_parallel_updates_and_support_are_denied = None
    test_same_person_second_invite_has_same_profile_and_pilot_closed = None
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.pool.execute((Path(__file__).resolve().parents[1] / 'migrations/046_crm_personal_telegram.sql').read_text(encoding='utf-8-sig'))
        await self.pool.execute('TRUNCATE crm_personal_telegram_events, crm_personal_telegram_state')
        self.login(123)
        await self.request_json('PUT', '/api/personal/profile', {'display_name':'Первый', 'timezone':'Europe/Moscow'})
        self.login(124)
        await self.request_json('PUT', '/api/personal/profile', {'display_name':'Второй', 'timezone':'Europe/Moscow'})
        self.bot = Bot('123456:test')
        self.calls = []
        async def fake_call(method, **kwargs):
            self.calls.append(method)
            return True
        self.bot_call = self.enterContext(patch.object(Bot, '__call__', side_effect=fake_call))
        self.make_dispatcher()

    def make_dispatcher(self):
        router = Router()
        install(router, lambda: self.pool)
        @router.message(F.text)
        async def existing(message):
            await message.answer('EXISTING', parse_mode=None)
        self.dp = Dispatcher()
        self.dp.include_router(router)

    async def feed_message(self, tg, msg_id, text, chat_type='private'):
        chat_id = tg if chat_type == 'private' else -12345
        payload = {'update_id': msg_id, 'message': {'message_id': msg_id, 'date': 1,
            'chat': {'id':chat_id, 'type':chat_type}, 'from': {'id':tg, 'is_bot':False, 'first_name':'Test'}, 'text':text}}
        if text.startswith('/'):
            payload['message']['entities'] = [{'type':'bot_command','offset':0,'length':len(text.split()[0])}]
        await self.dp.feed_update(self.bot, Update.model_validate(payload, context={'bot': self.bot}))

    async def feed_callback(self, tg, callback_id, data, chat_type='private'):
        chat_id = tg if chat_type == 'private' else -12345
        payload = {'update_id':10000, 'callback_query': {'id': callback_id, 'chat_instance':'test',
            'from': {'id':tg,'is_bot':False,'first_name':'Test'}, 'data':data,
            'message': {'message_id':900,'date':1,'chat':{'id':chat_id,'type':chat_type}}}}
        await self.dp.feed_update(self.bot, Update.model_validate(payload, context={'bot': self.bot}))

    async def test_create_http_edit_and_restart(self):
        await self.feed_callback(123, 'new1', 'pt:new')
        self.make_dispatcher()  # database state survives process restart
        await self.feed_message(123, 10, 'Задача из Telegram')
        self.login(123)
        code, result = await self.request_json('GET', '/api/personal/tasks')
        self.assertEqual(code, 200)
        task = result['tasks'][0]
        self.assertEqual(task['title'], 'Задача из Telegram')
        await self.feed_message(123, 10, 'Задача из Telegram')
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_tasks'), 1)
        code, result = await self.request_json('PATCH', f"/api/personal/tasks/{task['id']}", {'version':1,'title':'Из CRM'})
        self.assertEqual(code, 200)
        self.calls.clear()
        await self.feed_callback(123, 'open1', f"pt:open:{task['id']}:2")
        self.assertTrue(any('Из CRM' in str(c) for c in self.calls))
        await self.feed_callback(123, 'status1', f"pt:status:done:{task['id']}:2")
        await self.feed_callback(123, 'status1', f"pt:status:done:{task['id']}:2")
        self.assertEqual(await self.pool.fetchval('SELECT version FROM crm_personal_tasks WHERE id=$1', task['id']), 3)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_task_history WHERE task_id=$1', task['id']), 3)
        await self.feed_callback(123, 'stale', f"pt:status:planned:{task['id']}:2")
        self.assertEqual(await self.pool.fetchval('SELECT version FROM crm_personal_tasks WHERE id=$1', task['id']), 3)
        self.login(124)
        _, other = await self.request_json('GET', '/api/personal/tasks')
        self.assertEqual(other['tasks'], [])
        await self.feed_callback(124, 'attack', f"pt:status:cancelled:{task['id']}:3")
        self.assertEqual(await self.pool.fetchval('SELECT version FROM crm_personal_tasks WHERE id=$1', task['id']), 3)

    async def test_restrictions_and_existing_text(self):
        await self.feed_callback(123, 'group', 'pt:new', 'group')
        await self.feed_callback(125, 'nonpilot', 'pt:new')
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_telegram_state'), 0)
        await self.pool.execute('UPDATE employees SET archived_at=now() WHERE telegram_user_id=123')
        await self.feed_callback(123, 'archived', 'pt:new')
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_telegram_state'), 0)
        await self.pool.execute('UPDATE employees SET archived_at=NULL WHERE telegram_user_id=123')
        await self.pool.execute('UPDATE crm_invites SET revoked_at=now() WHERE telegram_user_id=123')
        await self.feed_callback(123, 'revoked', 'pt:new')
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_telegram_state'), 0)
        self.calls.clear()
        await self.feed_message(124, 45, 'Обычный вопрос')
        self.assertTrue(any('EXISTING' in str(c) for c in self.calls))

    async def test_concurrent_messages_consume_one_state_and_duplicate_callback_preserves_it(self):
        await self.feed_callback(123, 'new-concurrent', 'pt:new')
        await self.feed_callback(123, 'new-concurrent', 'pt:new')
        await asyncio.gather(self.feed_message(123, 101, 'Первая'), self.feed_message(123, 102, 'Вторая'))
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_tasks'), 1)
        await self.feed_callback(123, 'new-next', 'pt:new')
        await self.feed_message(123, 101, 'Первая')
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_telegram_state WHERE telegram_user_id=123'), 1)

    async def test_setup_preserves_existing_profile(self):
        await self.pool.execute('DELETE FROM crm_personal_profiles WHERE telegram_user_id=123')
        await self.feed_message(123, 120, '/tasks')
        profile = await self.pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=123')
        self.assertEqual(profile['timezone'], 'Europe/Moscow')
        await self.pool.execute("UPDATE crm_personal_profiles SET display_name='Своё имя', timezone='Asia/Tokyo' WHERE telegram_user_id=123")
        await self.feed_message(123, 121, '/tasks')
        profile = await self.pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=123')
        self.assertEqual((profile['display_name'], profile['timezone']), ('Своё имя', 'Asia/Tokyo'))

    async def test_pagination_long_text_and_clear(self):
        self.login(123)
        _, data = await self.request_json('POST', '/api/personal/tasks', {'title':'Большая', 'blocker':'x' * 4000, 'result_note':'y' * 4000})
        task = data['task']
        for i in range(11):
            await self.request_json('POST', '/api/personal/tasks', {'title':f'Задача {i}'})
        self.calls.clear()
        await self.feed_message(123, 130, '/tasks')
        self.assertTrue(any('pt:page:1' in str(c) for c in self.calls))
        self.calls.clear()
        await self.feed_callback(123, 'long', f"pt:open:{task['id']}:1")
        texts = [getattr(c, 'text', '') or '' for c in self.calls]
        self.assertLessEqual(max(map(len, texts)), 4096)
        await self.feed_callback(123, 'edit-clear', f"pt:edit:blocker:{task['id']}:1")
        await self.feed_message(123, 131, '-')
        self.assertEqual(await self.pool.fetchval('SELECT blocker FROM crm_personal_tasks WHERE id=$1', task['id']), '')

    async def test_real_demo_router_pauses_prompt_draft(self):
        import demo_bot
        from review import prompts
        dp = Dispatcher()
        dp.include_router(demo_bot.router)
        self.dp = dp
        fake_draft = {'kind':'review', 'body':'Черновик', 'history':[], 'start_body':'Черновик'}
        with patch.object(demo_bot, 'PG_POOL', self.pool), \
             patch.object(prompts, 'is_editor', return_value=True), \
             patch.object(prompts, 'draft', new=AsyncMock(return_value=fake_draft)), \
             patch.object(prompts, 'edit', new=AsyncMock()) as edit, \
             patch.object(demo_bot.rop_agent, 'answer', new=AsyncMock(return_value='Ответ')):
            await self.feed_message(123, 151, '/tasks')
            await self.feed_callback(123, 'real-new', 'pt:new')
            await self.feed_message(123, 152, 'Название задачи')
            edit.assert_not_awaited()
            row = await self.pool.fetchrow('SELECT title FROM crm_personal_tasks ORDER BY id DESC LIMIT 1')
            self.assertEqual(row['title'], 'Название задачи')
            self.assertEqual(await self.pool.fetchval('SELECT action FROM crm_personal_telegram_state WHERE telegram_user_id=123'), 'idle')
            await self.feed_message(123, 153, 'Обычный вопрос')
            edit.assert_not_awaited()
            from review import prompt_ui
            with patch.object(prompt_ui, 'editor_actor', new=AsyncMock(return_value=object())), \
                 patch.object(prompts, 'seed', new=AsyncMock()):
                await self.feed_message(123, 154, '/prompt')
            self.assertIsNone(await self.pool.fetchval('SELECT action FROM crm_personal_telegram_state WHERE telegram_user_id=123'))
            edit.assert_not_awaited()

    async def test_same_state_token_can_be_consumed_only_once(self):
        from crm.personal import save_task
        await self.feed_callback(123, 'force-race', 'pt:new')
        state = await self.pool.fetchrow('SELECT * FROM crm_personal_telegram_state WHERE telegram_user_id=123')
        profile = await self.pool.fetchrow('SELECT * FROM crm_personal_profiles WHERE telegram_user_id=123')
        token = (123, state['token'])
        outcomes = await asyncio.gather(
            save_task(self.pool, profile['id'], {'title':'Один'}, event_key='test:race:1', state_identity=token),
            save_task(self.pool, profile['id'], {'title':'Два'}, event_key='test:race:2', state_identity=token))
        self.assertEqual(sum(row is not None for row in outcomes), 1)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_tasks'), 1)
