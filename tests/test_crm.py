"""CRM: тестовые входы по ролям, очередь, итоги, звонок через Mango, кабинет клиента. Данные вымышленные."""
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

from crm import auth, cabinet, dial, gsheets, journal, recording, store
from review import callbacks, engine
from review import store as review_store
from tests.test_review import CALLBACK, LOST, TRANSCRIPT, WON
from tools import Actor, Forbidden

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')
MSK = ZoneInfo('Europe/Moscow')
MANAGER = Actor(123, 1, 'manager', '101')
OTHER = Actor(124, 1, 'manager', '102')
HEAD = Actor(901, 1, 'head', 'head')
TEST_KEY = 'k' * 20
SUMMARY = [['Тарифы'], ['Вид звонка', 'Цена'], ['Первичный звонок', '10 ₽'], ['Повторный звонок (после НДЗ)', '5 ₽'],
           ['Звонок с результатом «Заинтересованный»', '150 ₽'], [], ['Клиент (название листа)', 'Первичные', 'Повторные',
           'Заинтересованные', 'Сумма'], ['Кухни', '3', '2', '1', '190 ₽'], ['Окна', '0', '0', '0', '0 ₽'], ['ИТОГО', '3']]
HEAD_ROW = ['id', 'Проект', 'Тег проекта', 'Телефон', 'Создано', 'Напоминание', 'Заметка', 'Комментарий',
            'Звонок 1 (первичный)', 'Звонок 2 (повторный)', 'Звонок 3 (повторный)', 'Сумма', 'Дата базы',
            'Дата звонка 1', 'Дата звонка 2', 'Дата звонка 3']
CLIENT = [HEAD_ROW,
          ['1', 'Кухни', 'сайт-а', '79990000001', '', '', '', 'хочет замер', 'НДЗ', 'Заинтересованный', '', '165',
           '10.10.2026', '10.10.2026', '11.10.2026', ''],
          ['2', 'Кухни', 'сайт-б', '79990000002', '', 'пт после 12', '', '', 'Перезвон', '', '', '10',
           '10.10.2026', '10.10.2026', '', ''],
          ['3', 'Кухни', 'сайт-а', '79990000003', '', '', '', '', 'НДЗ', 'НДЗ 2', '', '15',
           '11.10.2026', '11.10.2026', '12.10.2026', ''],
          ['4', 'Кухни', '', '79990000004', '', '', '', '', '', '', '', '', '12.10.2026', '', '', ''],
          ['', 'итог']]


async def fake_values(sheet, range_):
    if sheet not in gsheets.allowed():
        raise PermissionError('Эта таблица CRM недоступна.')
    if range_.startswith('Сводка'):
        return SUMMARY
    if range_.startswith("'Кухни'"):
        return CLIENT
    raise LookupError('Такого листа нет.')


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

    def test_report_html_keeps_only_simple_tags(self):
        out = journal.html('<b>Итог</b> &lt;script&gt;x&lt;/script&gt; <a href="tg://x">ссылка</a> <i>ок</i> 5 &amp; 6')
        self.assertEqual(out, '<b>Итог</b> &lt;script&gt;x&lt;/script&gt; ссылка <i>ок</i> 5 &amp; 6')

    def test_parse_summary_and_client(self):
        info = cabinet.parse_summary(SUMMARY)
        self.assertEqual([t['price'] for t in info['tariffs']], [10, 5, 150])
        self.assertEqual([c['name'] for c in info['clients']], ['Кухни', 'Окна'])
        self.assertEqual(info['clients'][0]['sum'], 190)
        c = cabinet.parse_client(CLIENT)
        self.assertEqual(c['counts'], {'total': 4, 'called': 3, 'not_called': 1, 'reached': 2, 'interested': 1,
                                       'callback': 1, 'not_relevant': 0, 'no_answer': 1, 'other': 0})
        self.assertEqual((c['cost'], c['since']), (190, '2026-10-10'))
        self.assertEqual(c['interested'][0]['phone'], '79990000001')
        self.assertEqual(c['interested'][0]['last'], '2026-10-11')
        self.assertEqual(c['callbacks'][0]['remind'], 'пт после 12')
        self.assertEqual(c['days'][0], {'day': '2026-10-12', 'calls': 1, 'repeat': 1})
        day11 = next(d for d in c['days'] if d['day'] == '2026-10-11')
        self.assertEqual((day11['calls'], day11['first'], day11['repeat'], day11['reached'], day11['interested']),
                         (2, 1, 1, 1, 1))


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
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS recording_id TEXT;
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS direction INTEGER DEFAULT 2;
            ALTER TABLE clients ADD COLUMN IF NOT EXISTS min_call_seconds INTEGER DEFAULT 30;
            ALTER TABLE clients ADD COLUMN IF NOT EXISTS vpbx_api_key_enc TEXT;
            ALTER TABLE clients ADD COLUMN IF NOT EXISTS vpbx_api_salt_enc TEXT;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS id BIGSERIAL;
            ALTER TABLE pending_train_assignments ADD COLUMN IF NOT EXISTS topic TEXT;
            ALTER TABLE pending_train_assignments ADD COLUMN IF NOT EXISTS mode TEXT;
            ALTER TABLE pending_train_assignments ADD COLUMN IF NOT EXISTS assigned_by_telegram_user_id BIGINT;""")
        for name in ('008_call_reviews.sql', '009_callback_tasks.sql', '014_crm.sql', '015_crm_roles.sql', '016_crm_access.sql', '017_crm_reports.sql', '018_crm_questions.sql'):
            await self.pool.execute((root / 'migrations' / name).read_text())
        await self.pool.execute('TRUNCATE clients, crm_login_tokens, crm_sessions, crm_docs, crm_invites, crm_reports, crm_questions CASCADE')
        from crypto_util import encrypt
        await self.pool.execute("INSERT INTO clients(id,timezone,processing_enabled,vpbx_api_key_enc,vpbx_api_salt_enc) "
                                "VALUES(1,'Europe/Moscow',true,$1,$2)", encrypt('key'), encrypt('salt'))
        await self.pool.execute("INSERT INTO employees VALUES(1,'101',123,'manager','Первый'),(1,'102',124,'manager','Второй'),"
                                "(1,'head',901,'head','Руководитель')")
        dial._last_dial.clear()
        recording._cache.clear()

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
        """Личная сессия сотрудника: личного входа на сайте пока нет, кладём её в базу сами."""
        token = 'personal-' + str(tg)
        await self.pool.execute("INSERT INTO crm_sessions(token_hash,telegram_user_id,expires_at) VALUES($1,$2,now()+'1 day')",
                                auth.digest(token), tg)
        self.http.session.cookie_jar.update_cookies({'crm_session': token})

    async def enter(self, role, **extra):
        r = await self.http.post('/api/test/login', json={'key': TEST_KEY, 'role': role, **extra})
        self.assertEqual(r.status, 403)  # без заголовка X-CRM
        r = await self.http.post('/api/test/login', json={'key': TEST_KEY, 'role': role, **extra}, headers={'X-CRM': '1'})
        self.assertEqual(r.status, 200, await r.text())
        return (await r.json())['go']

    async def test_test_link_and_three_entries(self):
        with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY, 'KITCHEN_SHEET_ID': 'leads'}), \
                patch('crm.gsheets.values', fake_values):
            self.assertEqual((await self.http.get('/api/me')).status, 401)
            self.assertEqual((await self.http.get('/t/wrong-key-wrong-key')).status, 404)
            r = await self.http.get('/t/' + TEST_KEY)
            self.assertEqual(r.status, 200)
            self.assertIn("frame-ancestors 'none'", r.headers['Content-Security-Policy'])
            opts = await (await self.http.get('/api/test/options?key=' + TEST_KEY)).json()
            self.assertEqual(([m['ext'] for m in opts['managers']], opts['clients']), (['101', '102'], ['Кухни', 'Окна']))
            r = await self.http.post('/api/test/login', json={'key': 'nope', 'role': 'director'}, headers={'X-CRM': '1'})
            self.assertEqual(r.status, 404)

            # директор: своя страница, таблицы через сервер, общая база
            self.assertEqual(await self.enter('director'), '/director')
            me = await (await self.http.get('/api/me')).json()
            self.assertEqual((me['role'], me['test'], me['can_dial']), ('director', True, False))
            r = await self.http.get('/director')
            self.assertEqual(r.status, 200)
            self.assertIn("'unsafe-inline'", r.headers['Content-Security-Policy'])
            self.assertEqual((await self.http.get('/director/shim.js')).status, 200)
            self.assertEqual((await self.http.get('/director/../web.py')).status, 404)
            r = await self.http.post('/api/gs', json={'spreadsheetId': 'leads', 'range': 'Сводка!A1:F80'}, headers={'X-CRM': '1'})
            self.assertEqual((await r.json())['values'][1][0], 'Вид звонка')
            r = await self.http.post('/api/gs', json={'spreadsheetId': 'other', 'range': 'A1'}, headers={'X-CRM': '1'})
            self.assertEqual(r.status, 403)
            clients = await (await self.http.get('/api/cabinet')).json()
            self.assertEqual(len(clients['clients']), 2)
            cab = await (await self.http.get('/api/cabinet?client=Кухни')).json()
            self.assertEqual(cab['counts']['interested'], 1)
            self.assertEqual((await self.http.get('/api/queue?ext=102')).status, 200)
            r = await self.http.post('/api/leads', json={'text': '89991112233', 'ext': '102'}, headers={'X-CRM': '1'})
            self.assertEqual(r.status, 403)  # тестовый вход только смотрит очереди

            # менеджер: только своя очередь и только просмотр
            await self.http.get('/test-exit')
            self.assertEqual((await self.http.get('/api/me')).status, 401)
            r = await self.http.post('/api/test/login', json={'key': TEST_KEY, 'role': 'manager', 'ext': '999'},
                                     headers={'X-CRM': '1'})
            self.assertEqual(r.status, 400)
            self.assertEqual(await self.enter('manager', ext='102'), '/')
            me = await (await self.http.get('/api/me')).json()
            self.assertEqual((me['role'], me['name'], me['ext'], me['readonly'], me['can_dial']),
                             ('manager', 'Второй', '102', True, False))
            self.assertEqual((await self.http.get('/api/queue')).status, 200)
            await store.add_leads(self.pool, 1, '101', '89991112233', 'ГЦК')
            q = await (await self.http.get('/api/queue?ext=101')).json()
            self.assertEqual(q['items'], [])  # чужой добавочный в запросе не действует
            self.assertEqual((await self.http.get('/director')).status, 403)
            self.assertEqual((await self.http.get('/api/cabinet')).status, 403)
            with patch('crm.dial.mango_client.callback', AsyncMock(return_value={'result': 1000})) as cb:
                r = await self.http.post('/api/dial', json={'number': '89991234567'}, headers={'X-CRM': '1'})
            self.assertEqual(r.status, 403)
            cb.assert_not_awaited()

            # клиент: только свой кабинет
            await self.http.get('/test-exit')
            r = await self.http.post('/api/test/login', json={'key': TEST_KEY, 'role': 'client', 'client': 'Чужой'},
                                     headers={'X-CRM': '1'})
            self.assertEqual(r.status, 400)
            self.assertEqual(await self.enter('client', client='Кухни'), '/')
            me = await (await self.http.get('/api/me')).json()
            self.assertEqual((me['role'], me['name']), ('client', 'Кухни'))
            cab = await (await self.http.get('/api/cabinet?client=Окна')).json()
            self.assertEqual((cab['name'], cab['counts']['total']), ('Кухни', 4))  # чужое имя в запросе не действует
            for path in ('/api/queue', '/api/tasks', '/api/leads', '/api/stats', '/director', '/api/docs?path=a/b'):
                self.assertEqual((await self.http.get(path)).status, 403, path)
            r = await self.http.post('/api/gs', json={'spreadsheetId': 'leads', 'range': 'Сводка!A1'}, headers={'X-CRM': '1'})
            self.assertEqual(r.status, 403)

    async def test_test_link_off_without_key(self):
        with patch.dict(os.environ, {'CRM_TEST_KEY': ''}):
            self.assertEqual((await self.http.get('/t/' + TEST_KEY)).status, 404)
        with patch.dict(os.environ, {'CRM_TEST_KEY': 'short'}):
            self.assertEqual((await self.http.get('/t/short')).status, 404)

    async def test_director_docs(self):
        with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY}):
            await self.enter('director')
        h = {'X-CRM': '1'}
        await self.http.post('/api/docs', json={'path': 'pipe/deals/a1', 'op': 'set', 'data': {'stage': 'new', 'x': {'a': 1}}}, headers=h)
        await self.http.post('/api/docs', json={'path': 'pipe/deals/a1', 'op': 'update', 'data': {'x': {'b': 2}}}, headers=h)
        await self.http.post('/api/docs', json={'path': 'pipe/deals/a2', 'op': 'set', 'data': {'stage': 'won'}}, headers=h)
        await self.http.post('/api/docs', json={'path': 'pipe/deals/a2/notes/n', 'op': 'set', 'data': {'t': 1}}, headers=h)
        got = await (await self.http.get('/api/docs?path=pipe/deals/a1')).json()
        self.assertEqual(got['data'], {'stage': 'new', 'x': {'a': 1, 'b': 2}})
        col = await (await self.http.get('/api/docs?collection=pipe/deals')).json()
        self.assertEqual(sorted(d['path'] for d in col['docs']), ['pipe/deals/a1', 'pipe/deals/a2'])
        await self.http.post('/api/docs', json={'path': 'pipe/deals/a1', 'op': 'delete'}, headers=h)
        self.assertIsNone((await (await self.http.get('/api/docs?path=pipe/deals/a1')).json())['data'])
        for bad in ('../x', 'a//b', 'a/b c', ''):
            r = await self.http.post('/api/docs', json={'path': bad, 'op': 'set', 'data': {}}, headers=h)
            self.assertEqual(r.status, 400, bad)

    async def test_recording(self):
        def call(ext, rec):
            return self.pool.fetchval(
                "INSERT INTO calls(client_id,extension,call_started_at,client_number,duration_seconds,recording_id) "
                "VALUES(1,$1,now(),'79990001122',80,$2) RETURNING id", ext, rec)
        mine, other, silent = await call('101', 'rec-1'), await call('102', 'rec-2'), await call('101', None)
        audio = b'ID3' + bytes(range(97))
        await self.login(123)
        with patch('crm.recording.mango_client.fetch_recording', AsyncMock(return_value=audio)) as fetch:
            r = await self.http.get(f'/api/recording/{mine}')
            self.assertEqual((r.status, r.headers['Content-Type'], r.headers['Accept-Ranges']), (200, 'audio/mpeg', 'bytes'))
            self.assertEqual(await r.read(), audio)
            r = await self.http.get(f'/api/recording/{mine}', headers={'Range': 'bytes=2-5'})
            self.assertEqual((r.status, r.headers['Content-Range'], await r.read()), (206, 'bytes 2-5/100', audio[2:6]))
            r = await self.http.get(f'/api/recording/{mine}', headers={'Range': 'bytes=-10'})
            self.assertEqual(await r.read(), audio[-10:])
            self.assertEqual(fetch.await_count, 1)  # перемотка не качает файл заново
            self.assertEqual(fetch.await_args.args[1:], ('salt', 'rec-1'))
            self.assertEqual((await self.http.get(f'/api/recording/{other}')).status, 403)
            r = await self.http.get(f'/api/recording/{silent}')
            self.assertEqual(r.status, 404)
            self.assertIn('нет записи', (await r.json())['error'])
            self.assertEqual((await self.http.get('/api/recording/999999')).status, 404)
        card = await (await self.http.get('/api/card?number=89990001122')).json()
        self.assertEqual({c['id']: c['rec'] for c in card['calls']}, {mine: True, other: False, silent: False})
        calls = await (await self.http.get('/api/calls')).json()
        self.assertEqual({c['id']: c['rec'] for c in calls['items']}, {mine: True, silent: False})
        with patch('crm.recording.mango_client.fetch_recording', AsyncMock(side_effect=RuntimeError('Mango 404'))):
            r = await self.http.get(f'/api/recording/{other}')
            self.assertEqual(r.status, 403)
            await self.http.post('/api/logout', json={}, headers={'X-CRM': '1'})
            await self.login(901)  # руководитель слушает любой звонок
            r = await self.http.get(f'/api/recording/{other}')
            self.assertEqual(r.status, 404)
            self.assertIn('Mango не отдал', (await r.json())['error'])
        with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY, 'KITCHEN_SHEET_ID': 'leads'}), \
                patch('crm.gsheets.values', fake_values):
            await self.enter('client', client='Кухни')
            self.assertEqual((await self.http.get(f'/api/recording/{mine}')).status, 403)

    async def test_review_with_timecodes(self):
        call_id = await self.pool.fetchval(
            "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number,duration_seconds,recording_id) "
            "VALUES(1,'101',$1,now(),'79990001122',94,'rec-1') RETURNING id", TRANSCRIPT)
        call = await self.pool.fetchrow('SELECT * FROM calls WHERE id=$1', call_id)
        async with self.pool.acquire() as conn:
            await review_store.save(conn, call, engine.validate(copy.deepcopy(LOST), TRANSCRIPT), 1.0)
        await self.login(123)
        r = await (await self.http.get(f'/api/review/{call_id}')).json()
        self.assertEqual((r['manager'], r['rec'], r['success'], r['chance']), ('Первый', True, False, 'normal'))
        miss = r['misses'][0]
        self.assertEqual((miss['label'], miss['timecode'], miss['at'], miss['speaker']),
                         ('не закреплён день перезвона', '00:05', 5, 'Клиент'))
        self.assertIn('девятого ноября', miss['say'])
        self.assertIn('[00:05]', r['summary'])
        self.assertEqual(r['details'], LOST['details'])
        self.assertEqual(r['next_step'], 'через месяц')
        self.assertEqual(r['transcript'][0]['who'], 'Менеджер')
        self.assertEqual([l['at'] for l in r['transcript']], sorted(l['at'] for l in r['transcript']))
        card = await (await self.http.get('/api/card?number=89990001122')).json()
        self.assertTrue(card['calls'][0]['review'])
        await self.http.post('/api/logout', json={}, headers={'X-CRM': '1'})
        await self.login(124)  # чужой менеджер разбор не видит
        self.assertEqual((await self.http.get(f'/api/review/{call_id}')).status, 403)
        await self.http.post('/api/logout', json={}, headers={'X-CRM': '1'})
        await self.login(901)
        self.assertEqual((await self.http.get(f'/api/review/{call_id}')).status, 200)

    async def test_personal_links(self):
        h = {'X-CRM': '1'}
        with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY, 'KITCHEN_SHEET_ID': 'leads', 'CRM_URL': 'https://crm.example'}), \
                patch('crm.gsheets.values', fake_values):
            await self.enter('director')
            r = await self.http.post('/api/access', json={'role': 'manager', 'person_ext': '101', 'ext': '102'}, headers=h)
            link = (await r.json())['link']
            self.assertTrue(link.startswith('https://crm.example/p/'))
            r = await self.http.post('/api/access', json={'role': 'client', 'client': 'Кухни'}, headers=h)
            client_link = (await r.json())['link']
            r = await self.http.post('/api/access', json={'role': 'client', 'client': 'Чужой'}, headers=h)
            self.assertEqual(r.status, 400)
            r = await self.http.post('/api/access', json={'role': 'manager', 'person_ext': '999'}, headers=h)
            self.assertEqual(r.status, 400)
            listed = (await (await self.http.get('/api/access')).json())['invites']
            self.assertEqual([(i['role'], i['name']) for i in listed], [('client', 'Кухни'), ('manager', 'Первый')])
            await self.http.get('/test-exit')

            # менеджер по личной ссылке: свой добавочный, звонить можно
            token = link.rsplit('/', 1)[1]
            r = await self.http.get('/p/' + token, allow_redirects=False)
            self.assertEqual((r.status, r.headers['Location']), (302, '/'))
            self.assertIn('SameSite=Lax', r.headers['Set-Cookie'])
            me = await (await self.http.get('/api/me')).json()
            self.assertEqual((me['role'], me['name'], me['ext'], me['test'], me['can_dial'], me['readonly']),
                             ('manager', 'Первый', '101', False, True, False))
            r = await self.http.post('/api/leads', json={'text': '89991112233'}, headers=h)
            self.assertEqual((await r.json())['added'], 1)
            self.assertEqual((await self.http.get('/api/access')).status, 403)
            self.assertEqual((await self.http.get('/p/wrong')).status, 404)

            # отзыв ссылки выкидывает сразу
            invite_id = next(i['id'] for i in listed if i['role'] == 'manager')
            await self.pool.execute('UPDATE crm_invites SET revoked_at=now() WHERE id=$1', invite_id)
            self.assertEqual((await self.http.get('/api/me')).status, 401)
            self.assertEqual((await self.http.get('/p/' + token)).status, 404)

            # клиент по своей ссылке видит только свой кабинет
            r = await self.http.get('/p/' + client_link.rsplit('/', 1)[1], allow_redirects=False)
            self.assertEqual(r.status, 302)
            cab = await (await self.http.get('/api/cabinet')).json()
            self.assertEqual(cab['name'], 'Кухни')
            self.assertEqual((await self.http.get('/api/queue')).status, 403)

    async def test_personal_link_dies_with_employee(self):
        token = await auth.invite_create(self.pool, 1, 'manager', '102')
        r = await self.http.get('/p/' + token, allow_redirects=False)
        self.assertEqual(r.status, 302)
        self.assertEqual((await self.http.get('/api/queue')).status, 200)
        await self.pool.execute("DELETE FROM employees WHERE extension='102'")
        self.assertEqual((await self.http.get('/api/queue')).status, 401)

    async def test_reports(self):
        await journal.save(self.pool, 1, 'day_rop', '<b>Итог дня</b>', calls=[(5, 'Первый · 10:00')])
        await journal.save(self.pool, 1, 'day_manager', 'Твой день: 3 звонка', extension='101')
        await journal.save(self.pool, 1, 'day_manager', 'День второго', extension='102')
        await journal.save(self.pool, 99, 'day_rop', 'чужой отдел')  # клиента 99 нет: запись не ломает рассылку
        await self.login(123)
        items = (await (await self.http.get('/api/reports')).json())['items']
        self.assertEqual([(i['kind'], i['html']) for i in items], [('day_manager', 'Твой день: 3 звонка')])
        await self.http.post('/api/logout', json={}, headers={'X-CRM': '1'})
        await self.login(901)
        items = (await (await self.http.get('/api/reports')).json())['items']
        self.assertEqual(len(items), 3)
        rop = next(i for i in items if i['kind'] == 'day_rop')
        self.assertEqual((rop['title'], rop['calls']), ('Итог дня по отделу', [{'id': 5, 'label': 'Первый · 10:00'}]))
        self.assertEqual({i['who'] for i in items if i['kind'] == 'day_manager'}, {'Первый', 'Второй'})

    async def test_trainings(self):
        h = {'X-CRM': '1'}
        await self.pool.execute(
            """INSERT INTO training_sessions(client_id,extension,mode,status,drill_state,transcript) VALUES
               (1,'101','drill','completed',$1,'[]'),(1,'102','dialog','completed','{}',$2)""",
            json.dumps({'results': [{'objection': 'Дорого', 'answer': 'Давайте посчитаем', 'passed': True, 'comment': 'ок'},
                                    {'objection': 'Нет времени', 'answer': 'Ладно', 'passed': False}]}),
            json.dumps([{'role': 'client', 'text': 'Алло'}, {'role': 'manager', 'text': 'Добрый день'}]))
        with patch.dict(os.environ, {'TRAIN_BOT_USERNAME': 'trainer_bot'}), \
                patch('crm.training.notify', AsyncMock(return_value=True)) as notify:
            await self.login(901)
            r = await self.http.post('/api/trainings', json={'person_ext': '101', 'mode': 'drill', 'topic': 'дорого'}, headers=h)
            self.assertEqual(r.status, 200, await r.text())
            notify.assert_awaited_once()
            self.assertEqual((await self.http.post('/api/trainings', json={'person_ext': '999', 'mode': 'drill'}, headers=h)).status, 400)
            team = await (await self.http.get('/api/trainings?ext=102')).json()
            self.assertEqual([t['ext'] for t in team['items']], ['102'])
            await self.http.post('/api/logout', json={}, headers=h)

            await self.login(123)
            mine = await (await self.http.get('/api/trainings?ext=102')).json()
            self.assertEqual([(t['ext'], t['result']) for t in mine['items']], [('101', 'зачтено 1 из 2')])
            self.assertEqual(len(mine['pending']), 1)
            self.assertTrue(mine['pending'][0]['link'].startswith('https://t.me/trainer_bot?start=train_'))
            one = await (await self.http.get(f"/api/trainings/{mine['items'][0]['id']}")).json()
            self.assertEqual([c['passed'] for c in one['checks']], [True, False])
            other = await self.pool.fetchval("SELECT id FROM training_sessions WHERE extension='102'")
            self.assertEqual((await self.http.get(f'/api/trainings/{other}')).status, 403)
            self.assertEqual((await self.http.post('/api/trainings', json={'person_ext': '101', 'mode': 'drill'}, headers=h)).status, 403)

            # «Потренировать» ошибку из своего разбора
            call_id = await self.pool.fetchval(
                "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number) VALUES(1,'101',$1,now(),'79990001122') RETURNING id",
                TRANSCRIPT)
            call = await self.pool.fetchrow('SELECT * FROM calls WHERE id=$1', call_id)
            async with self.pool.acquire() as conn:
                await review_store.save(conn, call, engine.validate(copy.deepcopy(LOST), TRANSCRIPT), 1.0)
            self.assertTrue((await (await self.http.get(f'/api/review/{call_id}')).json())['can_train'])
            r = await (await self.http.post(f'/api/review/{call_id}/train', json={}, headers=h)).json()
            self.assertTrue(r['link'].startswith('https://t.me/trainer_bot?start=train_'))
            self.assertEqual(await self.pool.fetchval('SELECT topic FROM pending_train_assignments ORDER BY id DESC LIMIT 1'),
                             f'review:{call_id}')

    async def test_ask_department(self):
        h = {'X-CRM': '1'}
        seen = []

        async def fake_answer(pool, who, question, allow_course=False):
            seen.append((who.role, who.extension, question))
            return '<b>Ответ</b> по отделу <script>'
        with patch('rop_agent.answer', fake_answer, create=True), patch.dict('sys.modules', {'rop_agent': type('M', (), {'answer': fake_answer})}):
            await self.login(123)
            r = await (await self.http.post('/api/ask', json={'question': 'Где я теряю?'}, headers=h)).json()
            self.assertEqual(r['html'], '<b>Ответ</b> по отделу &lt;script&gt;')
            self.assertEqual(seen, [('manager', '101', 'Где я теряю?')])
            self.assertEqual((await self.http.post('/api/ask', json={'question': ' '}, headers=h)).status, 400)
            await self.http.post('/api/logout', json={}, headers=h)
            await self.login(901)
            self.assertEqual((await (await self.http.get('/api/ask')).json())['items'], [])  # у руководителя своя история
            await self.http.post('/api/ask', json={'question': 'Темы на планёрку'}, headers=h)
            self.assertEqual(len((await (await self.http.get('/api/ask')).json())['items']), 1)
            await self.http.post('/api/logout', json={}, headers=h)
            with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY}):
                await self.enter('manager', ext='101')
            self.assertEqual((await self.http.post('/api/ask', json={'question': 'x'}, headers=h)).status, 403)

    async def test_personal_session_and_logout(self):
        await self.login(123)
        me = await (await self.http.get('/api/me')).json()
        self.assertEqual((me['name'], me['ext'], me['can_dial'], me['privileged'], me['test']), ('Первый', '101', True, False, False))
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

