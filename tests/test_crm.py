"""CRM менеджера: вход, очередь, итоги, звонок через Mango. Данные вымышленные."""
import copy
import json
import os
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import asyncpg

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
os.environ.setdefault('ENCRYPTION_KEY', 'q7m8b4gN0bO2PZqvG6b0yC3rA1D5pYc8tW2XkL9uQeE=')

from crm import auth, dial, store
from review import callbacks, engine
from review import store as review_store
from tests.test_review import CALLBACK, WON
from tools import Actor, Forbidden

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')
MSK = ZoneInfo('Europe/Moscow')
MANAGER = Actor(123, 1, 'manager', '101')
OTHER = Actor(124, 1, 'manager', '102')
HEAD = Actor(901, 1, 'head', 'head')


class PureTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(dial.normalize('+7 (999) 123-45-67'), '79991234567')
        self.assertEqual(dial.normalize('89991234567'), '79991234567')
        self.assertEqual(dial.normalize('9991234567'), '79991234567')
        self.assertEqual(dial.normalize('8 495 123 45 67'), '74951234567')
        self.assertEqual(dial.normalize('88001234567'), '78001234567')
        for bad in ('88031234567', '78091234567', '12345', '+1 202 555 0101', '79991234567890', None, '71991234567'):
            self.assertIsNone(dial.normalize(bad), bad)

    def test_parse_leads(self):
        found, bad = store.parse_leads('89991234567; Иван; ООО Ромашка; после обеда\n'
                                       'ООО Лето, +7 916 000-11-22\n\nпросто текст\n  9031112233  ')
        self.assertEqual([f['number'] for f in found], ['79991234567', '79160001122', '79031112233'])
        self.assertEqual((found[0]['name'], found[0]['company'], found[0]['note']), ('Иван', 'ООО Ромашка', 'после обеда'))
        self.assertEqual(found[1]['name'], 'ООО Лето')
        self.assertIsNone(found[2]['name'])
        self.assertEqual(bad, ['просто текст'])


class DBCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        self.assertTrue((await self.pool.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        root = Path(__file__).resolve().parents[1]
        if not await self.pool.fetchval("SELECT to_regclass('clients') IS NOT NULL"):
            await self.pool.execute((root / 'tests/bootstrap.sql').read_text())
        await self.pool.execute("""
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS duration_seconds INTEGER DEFAULT 90;
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS client_number TEXT;
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS direction INTEGER DEFAULT 2;
            ALTER TABLE clients ADD COLUMN IF NOT EXISTS min_call_seconds INTEGER DEFAULT 30;
            ALTER TABLE clients ADD COLUMN IF NOT EXISTS vpbx_api_key_enc TEXT;
            ALTER TABLE clients ADD COLUMN IF NOT EXISTS vpbx_api_salt_enc TEXT;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS id BIGSERIAL;
            ALTER TABLE pending_train_assignments ADD COLUMN IF NOT EXISTS topic TEXT;
            ALTER TABLE pending_train_assignments ADD COLUMN IF NOT EXISTS mode TEXT;
            ALTER TABLE pending_train_assignments ADD COLUMN IF NOT EXISTS assigned_by_telegram_user_id BIGINT;""")
        for name in ('008_call_reviews.sql', '009_callback_tasks.sql', '014_crm.sql'):
            await self.pool.execute((root / 'migrations' / name).read_text())
        await self.pool.execute('TRUNCATE clients, crm_login_tokens, crm_sessions CASCADE')
        from crypto_util import encrypt
        await self.pool.execute("INSERT INTO clients(id,timezone,processing_enabled,vpbx_api_key_enc,vpbx_api_salt_enc) "
                                "VALUES(1,'Europe/Moscow',true,$1,$2)", encrypt('key'), encrypt('salt'))
        await self.pool.execute("INSERT INTO employees VALUES(1,'101',123,'manager','Первый'),(1,'102',124,'manager','Второй'),"
                                "(1,'head',901,'head','Руководитель')")
        dial._last_dial.clear()

    async def asyncTearDown(self):
        await self.pool.close()

    def today(self):
        return datetime.now(MSK).date()


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class StoreTests(DBCase):
    async def test_queue_retries_no_answer_and_stops_after_three(self):
        res = await store.add_leads(self.pool, 1, '101', '89991234567\n89991234567; Иван\n89160001122\nмусор', 'ГЦК')
        self.assertEqual((res['added'], res['duplicates'], res['invalid']), (2, 1, 1))
        self.assertEqual(await self.pool.fetchval("SELECT name FROM crm_leads WHERE number='79991234567'"), 'Иван')
        again = await store.add_leads(self.pool, 1, '101', '+7 999 123 45 67', None)
        self.assertEqual((again['added'], again['duplicates']), (0, 1))
        q = await store.queue(self.pool, 1, '101')
        self.assertEqual([i['number'] for i in q['items']], ['79991234567', '79160001122'])
        self.assertEqual((await store.queue(self.pool, 1, '102'))['items'], [])

        await store.outcome(self.pool, MANAGER, 1, '101', '79991234567', 'no_answer')
        q = await store.queue(self.pool, 1, '101')
        self.assertEqual([i['number'] for i in q['items']], ['79160001122'])
        self.assertEqual(q['later'], 1)
        later = datetime.now(timezone.utc) + timedelta(hours=3)
        q = await store.queue(self.pool, 1, '101', now=later)
        self.assertEqual(q['items'][0]['number'], '79991234567')  # недозвоны идут первыми
        self.assertEqual(q['items'][0]['attempts'], 1)
        for _ in range(2):
            await store.outcome(self.pool, MANAGER, 1, '101', '79991234567', 'no_answer')
        q = await store.queue(self.pool, 1, '101', now=later + timedelta(hours=5))
        self.assertEqual([i['number'] for i in q['items']], ['79160001122'])

    async def test_callback_outcome_creates_task_and_survives_review(self):
        await store.add_leads(self.pool, 1, '101', '79990001122; Пётр; ООО Ромашка', None)
        started = datetime.now(timezone.utc) - timedelta(minutes=5)
        call_id = await self.pool.fetchval(
            "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number) VALUES(1,'101',$1,$2,'79990001122') RETURNING id",
            CALLBACK, started)
        with self.assertRaises(ValueError):
            await store.outcome(self.pool, MANAGER, 1, '101', '79990001122', 'callback')
        tomorrow = self.today() + timedelta(days=1)
        res = await store.outcome(self.pool, MANAGER, 1, '101', '79990001122', 'callback', 'просил после обеда',
                                  tomorrow, time(15, 0))
        task = await self.pool.fetchrow('SELECT * FROM callback_tasks WHERE id=$1', res['task_id'])
        self.assertEqual((task['source'], task['kind'], task['due_date'], task['company'], task['raw']),
                         ('crm', 'callback', tomorrow, 'ООО Ромашка', 'просил после обеда'))
        self.assertIsNone(task['call_id'])
        lead = await self.pool.fetchrow("SELECT * FROM crm_leads WHERE number='79990001122'")
        self.assertEqual(lead['status'], 'callback')
        self.assertEqual((await store.queue(self.pool, 1, '101'))['items'], [])  # задача на завтра, номер не дублируется

        # Разбор этого же звонка приходит позже: задачу из CRM не закрывает и второй не создаёт.
        data = engine.validate(copy.deepcopy({**WON, 'client': {'next_step': {
            'type': 'callback', 'date': tomorrow.isoformat(), 'time': '11:00', 'raw': 'в пятницу'}}}), CALLBACK)
        call = await self.pool.fetchrow('SELECT * FROM calls WHERE id=$1', call_id)
        async with self.pool.acquire() as conn:
            await review_store.save(conn, call, data, 1.0)
            self.assertIsNone(await callbacks.record(conn, call, data))
        rows = await self.pool.fetch("SELECT status, source FROM callback_tasks WHERE client_number='79990001122'")
        self.assertEqual([(r['status'], r['source']) for r in rows], [('open', 'crm')])

        # Следующий звонок закрывает старую задачу из CRM, как обычно.
        call2 = await self.pool.fetchval(
            "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number) VALUES(1,'101',$1,$2,'79990001122') RETURNING id",
            CALLBACK, datetime.now(timezone.utc) + timedelta(minutes=1))
        call2 = await self.pool.fetchrow('SELECT * FROM calls WHERE id=$1', call2)
        async with self.pool.acquire() as conn:
            await review_store.save(conn, call2, data, 1.0)
            self.assertIsNotNone(await callbacks.record(conn, call2, data))
        self.assertEqual(await self.pool.fetchval(
            "SELECT status FROM callback_tasks WHERE id=$1", res['task_id']), 'closed_by_call')

    async def test_review_task_in_queue_and_meeting_closes_it(self):
        call_id = await self.pool.fetchval(
            "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number) VALUES(1,'101',$1,$2,'79990003344') RETURNING id",
            CALLBACK, datetime.now(timezone.utc) - timedelta(days=2))
        task_id = await self.pool.fetchval(
            """INSERT INTO callback_tasks(client_id,extension,call_id,client_number,company,kind,due_date,raw)
               VALUES(1,'101',$1,'79990003344','ООО Лето','callback',$2,'наберите во вторник') RETURNING id""",
            call_id, self.today() - timedelta(days=1))
        await store.add_leads(self.pool, 1, '101', '79990003344\n79990005566', None)
        q = await store.queue(self.pool, 1, '101')
        self.assertEqual([(i['kind'], i['number']) for i in q['items']], [('task', '79990003344'), ('lead', '79990005566')])
        self.assertTrue(q['items'][0]['late'])
        self.assertEqual(q['items'][0]['title'], 'ООО Лето')

        await store.outcome(self.pool, MANAGER, 1, '101', '79990003344', 'no_answer')
        self.assertEqual([i['number'] for i in (await store.queue(self.pool, 1, '101'))['items']], ['79990005566'])

        day = self.today() + timedelta(days=3)
        res = await store.outcome(self.pool, MANAGER, 1, '101', '79990003344', 'meeting', None, day, time(12, 30))
        self.assertEqual(await self.pool.fetchval('SELECT status FROM callback_tasks WHERE id=$1', task_id), 'done')
        new = await self.pool.fetchrow('SELECT * FROM callback_tasks WHERE id=$1', res['task_id'])
        self.assertEqual((new['kind'], new['due_time'], new['company']), ('meeting', time(12, 30), 'ООО Лето'))
        tasks = await store.tasks(self.pool, 1, '101')
        self.assertEqual([t['id'] for t in tasks['items']], [res['task_id']])

    async def test_change_task_and_scope(self):
        task_id = await self.pool.fetchval(
            """INSERT INTO callback_tasks(client_id,extension,call_id,client_number,kind,due_date,source)
               VALUES(1,'101',NULL,'79990007788','callback',$1,'crm') RETURNING id""", self.today())
        with self.assertRaises(Forbidden):
            await store.change_task(self.pool, OTHER, 1, '102', task_id, 'done')
        with self.assertRaises(ValueError):
            await store.change_task(self.pool, MANAGER, 1, '101', task_id, 'move', self.today() - timedelta(days=1))
        await store.change_task(self.pool, MANAGER, 1, '101', task_id, 'move', self.today() + timedelta(days=2), time(10, 0))
        row = await self.pool.fetchrow('SELECT due_date, due_time FROM callback_tasks WHERE id=$1', task_id)
        self.assertEqual((row['due_date'], row['due_time']), (self.today() + timedelta(days=2), time(10, 0)))
        await store.change_task(self.pool, MANAGER, 1, '101', task_id, 'done')
        with self.assertRaises(ValueError):
            await store.change_task(self.pool, MANAGER, 1, '101', task_id, 'cancelled')

        self.assertEqual(await store.scope(self.pool, MANAGER, '102'), '101')
        self.assertEqual(await store.scope(self.pool, HEAD, None), '101')
        self.assertEqual(await store.scope(self.pool, HEAD, '102'), '102')
        with self.assertRaises(Forbidden):
            await store.scope(self.pool, HEAD, 'head')

    async def test_card_hides_colleague_reviews(self):
        for ext in ('101', '102'):
            call_id = await self.pool.fetchval(
                "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number) VALUES(1,$1,$2,now(),'79990009900') RETURNING id",
                ext, CALLBACK)
            call = await self.pool.fetchrow('SELECT * FROM calls WHERE id=$1', call_id)
            async with self.pool.acquire() as conn:
                await review_store.save(conn, call, engine.validate(copy.deepcopy(WON), CALLBACK), 1.0)
        mine = await store.card(self.pool, MANAGER, 1, '101', '8 999 000 99 00')
        by_ext = {c['ext']: c for c in mine['calls']}
        self.assertEqual(by_ext['102']['who'], 'Второй')
        self.assertIsNone(by_ext['102']['review'])
        self.assertTrue(by_ext['101']['review']['manager_text'])
        head = await store.card(self.pool, HEAD, 1, '101', '79990009900')
        by_ext = {c['ext']: c for c in head['calls']}
        self.assertTrue(by_ext['102']['review']['rop_line'])
        self.assertNotIn('manager_text', by_ext['102']['review'])

    async def test_stats(self):
        now = datetime.now(timezone.utc)
        await self.pool.execute(
            """INSERT INTO calls(client_id,extension,direction,duration_seconds,call_started_at,status) VALUES
               (1,'101',2,0,$1,'skipped'),(1,'101',2,10,$1,'skipped'),(1,'101',2,120,$1,'new'),
               (1,'101',1,0,$1,'skipped'),(1,'102',2,50,$1,'new')""", now)
        await store.outcome(self.pool, MANAGER, 1, '101', '79990001122', 'not_interested')
        data = await store.stats(self.pool, 1, '101', 1)
        self.assertEqual(len(data['rows']), 1)
        row = data['rows'][0]
        self.assertEqual((row['dials'], row['answered'], row['talks'], row['incoming'], row['missed'], row['seconds']),
                         (3, 2, 1, 1, 1, 130))
        self.assertEqual(row['outcomes'], {'not_interested': 1})
        everyone = await store.stats(self.pool, 1, None, 1)
        self.assertEqual(sorted(r['ext'] for r in everyone['rows']), ['101', '102'])


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class WebTests(DBCase):
    """Через HTTP: вход по ссылке, защита от чужих запросов, звонок."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from aiohttp.test_utils import TestClient, TestServer
        from crm import web
        web.SECURE_COOKIE = False
        self.http = TestClient(TestServer(web.build(self.pool)))
        await self.http.start_server()

    async def asyncTearDown(self):
        await self.http.close()
        await super().asyncTearDown()

    async def login(self, tg):
        token = await auth.issue_login(self.pool, tg)
        r = await self.http.post('/api/login', json={'token': token})
        self.assertEqual(r.status, 403)  # без заголовка X-CRM
        r = await self.http.post('/api/login', json={'token': token}, headers={'X-CRM': '1'})
        self.assertEqual(r.status, 200)
        r = await self.http.post('/api/login', json={'token': token}, headers={'X-CRM': '1'})
        self.assertEqual(r.status, 401)  # токен одноразовый
        return token

    async def test_login_me_and_logout(self):
        r = await self.http.get('/api/me')
        self.assertEqual(r.status, 401)
        r = await self.http.get('/in/whatever')
        self.assertEqual(r.status, 200)
        self.assertIn("frame-ancestors 'none'", r.headers['Content-Security-Policy'])
        await self.login(123)
        me = await (await self.http.get('/api/me')).json()
        self.assertEqual((me['name'], me['ext'], me['can_dial'], me['privileged']), ('Первый', '101', True, False))
        r = await self.http.post('/api/logout', json={}, headers={'X-CRM': '1'})
        self.assertEqual(r.status, 200)
        self.assertEqual((await self.http.get('/api/me')).status, 401)

    async def test_removed_employee_loses_session(self):
        await self.login(124)
        self.assertEqual((await self.http.get('/api/queue')).status, 200)
        await self.pool.execute("UPDATE employees SET telegram_user_id=NULL WHERE extension='102'")
        self.assertEqual((await self.http.get('/api/queue')).status, 401)

    async def test_dial_through_mango(self):
        await self.login(123)
        with patch('crm.dial.mango_client.callback', AsyncMock(return_value={'result': 1000})) as cb:
            r = await self.http.post('/api/dial', json={'number': '8 999 123-45-67', 'ext': '102'}, headers={'X-CRM': '1'})
            self.assertEqual(r.status, 200, await r.text())
            body = await r.json()
            self.assertEqual(cb.await_args.args[1:4], ('salt', '101', '79991234567'))  # свой добавочный, не из запроса
            r = await self.http.post('/api/dial', json={'number': '89991234567'}, headers={'X-CRM': '1'})
            self.assertEqual(r.status, 400)  # второй звонок сразу же
        r = await self.http.post('/api/outcome', json={'number': '79991234567', 'outcome': 'proposal',
                                                       'dial_id': body['dial_id']}, headers={'X-CRM': '1'})
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual(await self.pool.fetchval('SELECT dial_id FROM crm_outcomes'), body['dial_id'])
        dial._last_dial.clear()
        with patch('crm.dial.mango_client.callback', AsyncMock(return_value={'result': 3330})):
            r = await self.http.post('/api/dial', json={'number': '89991234567'}, headers={'X-CRM': '1'})
        self.assertEqual(r.status, 400)
        self.assertIn('3330', (await r.json())['error'])
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_dials'), 2)

    async def test_head_sees_team_but_cannot_dial(self):
        await self.login(901)
        me = await (await self.http.get('/api/me')).json()
        self.assertEqual([p['ext'] for p in me['team']], ['101', '102'])
        self.assertFalse(me['can_dial'])
        r = await self.http.post('/api/leads', json={'text': '89991112233', 'ext': '102'}, headers={'X-CRM': '1'})
        self.assertEqual((await r.json())['added'], 1)
        q = await (await self.http.get('/api/queue?ext=102')).json()
        self.assertEqual(len(q['items']), 1)
        r = await self.http.post('/api/dial', json={'number': '89991112233'}, headers={'X-CRM': '1'})
        self.assertEqual(r.status, 400)
        r = await self.http.get('/api/queue?ext=999')
        self.assertEqual(r.status, 403)

