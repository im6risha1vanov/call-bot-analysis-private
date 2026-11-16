import os
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
os.environ.setdefault('ENCRYPTION_KEY', 'q7m8b4gN0bO2PZqvG6b0yC3rA1D5pYc8tW2XkL9uQeE=')
from crm import personal


class ValidationTests(unittest.TestCase):
    def test_dates_are_distinct_and_timezone_is_required(self):
        parsed = personal.parse_task({'title': 'Задача', 'planned_day': '2026-11-20', 'due_at': '2026-11-21T12:00:00+03:00', 'reminder_at': '2026-11-19T12:00:00Z'})
        self.assertEqual(parsed['planned_day'], date(2026, 11, 20))
        self.assertNotEqual(parsed['due_at'], parsed['reminder_at'])
        with self.assertRaises(ValueError):
            personal.parse_task({'title': 'x', 'due_at': '2026-11-21T12:00:00'})

    def test_status_and_unexpected_fields_rejected(self):
        for payload in ({'title': 'x', 'status': 'other'}, {'title': 'x', 'status': []}, {'title': 'x', 'status': {}}, {'title': 'x', 'owner_id': 5}, {'title': ''}):
            with self.assertRaises(ValueError):
                personal.parse_task(payload)


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_by_default_and_verified_against_invite(self):
        pool = SimpleNamespace(fetchval=AsyncMock(return_value=1))
        actor = SimpleNamespace(telegram_user_id=123, client_id=1)
        who = SimpleNamespace(test=False, support=False, support_by=None, invite_id=3, actor=actor)
        with patch.dict(os.environ, {'CRM_PERSONAL_PILOT_TELEGRAM_IDS': ''}):
            self.assertIsNone(await personal.identity(pool, who))
        with patch.dict(os.environ, {'CRM_PERSONAL_PILOT_TELEGRAM_IDS': '123'}):
            self.assertEqual(await personal.identity(pool, who), 123)
            pool.fetchval.assert_awaited_with(unittest.mock.ANY, 3, 123, 1)
            pool.fetchval.return_value = None
            self.assertIsNone(await personal.identity(pool, who))

    async def test_support_and_test_sessions_are_denied(self):
        pool = SimpleNamespace(fetchval=AsyncMock(return_value=1))
        actor = SimpleNamespace(telegram_user_id=123, client_id=1)
        with patch.dict(os.environ, {'CRM_PERSONAL_PILOT_TELEGRAM_IDS': '123'}):
            for test, support, support_by in [(True, False, None), (False, True, None), (False, False, 9)]:
                who = SimpleNamespace(test=test, support=support, support_by=support_by, invite_id=3, actor=actor)
                self.assertIsNone(await personal.identity(pool, who))
        pool.fetchval.assert_not_awaited()

from pathlib import Path
from tests.test_crm import DBCase, DSN


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class PersonalHTTPTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from aiohttp.test_utils import TestClient, TestServer
        from crm import web
        await self.pool.execute((Path(__file__).resolve().parents[1] / 'migrations/045_crm_personal_agent.sql').read_text(encoding='utf-8'))
        await self.pool.execute('TRUNCATE crm_personal_profiles CASCADE')
        self.enterContext(patch.dict(os.environ, {'CRM_PERSONAL_PILOT_TELEGRAM_IDS': '123,124'}))
        web.SECURE_COOKIE = False
        self.http = TestClient(TestServer(web.build(self.pool)))
        await self.http.start_server()
        self.headers = {'X-CRM': '1'}
        from crm import auth
        for tg, ext in ((123, '101'), (124, '102')):
            invite = await self.pool.fetchval('''INSERT INTO crm_invites(token_hash,role,client_id,extension,telegram_user_id)
                VALUES($1,'manager',1,$2,$3) RETURNING id''', 'invite'+str(tg), ext, tg)
            await self.pool.execute('''INSERT INTO crm_sessions(token_hash,telegram_user_id,expires_at,role,client_id,extension,is_test,invite_id)
                VALUES($1,$2,now()+interval '1 day','manager',1,$3,false,$4)''', auth.digest('session'+str(tg)), tg, ext, invite)

    async def asyncTearDown(self):
        await self.http.close()
        await super().asyncTearDown()

    def login(self, tg):
        self.http.session.cookie_jar.update_cookies({'crm_session': 'session'+str(tg)})

    async def request_json(self, method, path, payload=None):
        response = await self.http.request(method, path, json=payload, headers=self.headers)
        return response.status, await response.json()

    async def test_owner_isolation_versions_and_history(self):
        self.login(123)
        code, _ = await self.request_json('GET', '/api/personal/tasks')
        self.assertEqual(code, 409)
        code, _ = await self.request_json('PUT', '/api/personal/profile', {'display_name': 'Первый', 'timezone': 'Europe/Moscow'})
        self.assertEqual(code, 200)
        code, project = await self.request_json('POST', '/api/personal/projects', {'title': 'Проект'})
        self.assertEqual(code, 201)
        code, result = await self.request_json('POST', '/api/personal/tasks', {'title': 'Задача', 'project_id': project['project']['id'], 'planned_day': '2026-11-20', 'due_at': '2026-11-21T10:00:00+03:00', 'reminder_at': '2026-11-19T10:00:00+03:00', 'expected_result': 'Готово'})
        self.assertEqual(code, 201, result)
        task = result['task']; task_id = task['id']
        code, result = await self.request_json('PATCH', f'/api/personal/tasks/{task_id}', {'version': 1, 'status': 'done', 'result_note': 'Сделано'})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['task']['version'], 2)
        code, _ = await self.request_json('PATCH', f'/api/personal/tasks/{task_id}', {'version': 1, 'status': 'planned'})
        self.assertEqual(code, 409)
        code, history = await self.request_json('GET', f'/api/personal/tasks/{task_id}/history')
        self.assertEqual([x['version'] for x in history['history']], [1, 2])
        self.login(124)
        await self.request_json('PUT', '/api/personal/profile', {'display_name': 'Второй', 'timezone': 'Europe/Moscow'})
        code, tasks = await self.request_json('GET', '/api/personal/tasks')
        self.assertEqual(tasks['tasks'], [])
        code, _ = await self.request_json('PATCH', f'/api/personal/tasks/{task_id}', {'version': 2, 'status': 'planned'})
        self.assertEqual(code, 404)
        code, _ = await self.request_json('GET', f'/api/personal/tasks/{task_id}/history')
        self.assertEqual(code, 404)
        code, _ = await self.request_json('POST', '/api/personal/tasks', {'title': 'Чужой проект', 'project_id': project['project']['id']})
        self.assertEqual(code, 404)

    async def test_parallel_updates_and_support_are_denied(self):
        import asyncio
        from crm import auth
        self.login(123)
        await self.request_json('PUT', '/api/personal/profile', {'display_name': 'Первый', 'timezone': 'Europe/Moscow'})
        _, created = await self.request_json('POST', '/api/personal/tasks', {'title': 'Параллельная задача'})
        task_id = created['task']['id']
        responses = await asyncio.gather(*[self.request_json('PATCH', f'/api/personal/tasks/{task_id}', {'version': 1, 'status': state}) for state in ('done', 'cancelled')])
        self.assertEqual(sorted(code for code, _ in responses), [200, 409])
        _, history = await self.request_json('GET', f'/api/personal/tasks/{task_id}/history')
        self.assertEqual([entry['version'] for entry in history['history']], [1, 2])
        admin = await self.pool.fetchval("INSERT INTO crm_invites(token_hash,role,client_id,platform) VALUES('support-preview','director',1,true) RETURNING id")
        await self.pool.execute('UPDATE crm_sessions SET support_by=$1 WHERE token_hash=$2', admin, auth.digest('session123'))
        code, _ = await self.request_json('GET', '/api/personal/tasks')
        self.assertEqual(code, 403)
        code, me = await self.request_json('GET', '/api/me')
        self.assertEqual(code, 200)
        self.assertFalse(me['personal_agent_enabled'])
        self.assertNotIn('tasks', me)

    async def test_same_person_second_invite_has_same_profile_and_pilot_closed(self):
        from crm import auth
        self.login(123)
        _, result = await self.request_json('PUT', '/api/personal/profile', {'display_name': 'Один человек', 'timezone': 'Europe/Moscow'})
        profile_id = result['profile']['id']
        invite = await self.pool.fetchval("INSERT INTO crm_invites(token_hash,role,client_id,extension,telegram_user_id) VALUES('second-invite','manager',1,'101',123) RETURNING id")
        await self.pool.execute("INSERT INTO crm_sessions(token_hash,telegram_user_id,expires_at,role,client_id,extension,is_test,invite_id) VALUES($1,123,now()+interval '1 day','manager',1,'101',false,$2)", auth.digest('second-session'), invite)
        self.http.session.cookie_jar.update_cookies({'crm_session': 'second-session'})
        code, result = await self.request_json('GET', '/api/personal/profile')
        self.assertEqual(code, 200)
        self.assertEqual(result['profile']['id'], profile_id)
        with patch.dict(os.environ, {'CRM_PERSONAL_PILOT_TELEGRAM_IDS': ''}):
            code, _ = await self.request_json('GET', '/api/personal/profile')
            self.assertEqual(code, 403)
