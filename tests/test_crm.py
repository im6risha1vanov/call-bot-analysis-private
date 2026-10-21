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

from crm import auth, cabinet, dial, gsheets, journal, recording, staff, store
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
        for name in ('008_call_reviews.sql', '009_callback_tasks.sql', '012_prompt_versions.sql', '013_employee_invites.sql',
                     '014_crm.sql', '015_crm_roles.sql', '016_crm_access.sql', '017_crm_reports.sql', '018_crm_questions.sql',
                     '019_crm_staff.sql', '021_manager_memory.sql', '022_presentations.sql', '024_deals.sql', '025_contracts.sql', '026_presentation_client.sql', '027_sources.sql'):
            await self.pool.execute((root / 'migrations' / name).read_text())
        await self.pool.execute('TRUNCATE clients, crm_login_tokens, crm_sessions, crm_docs, crm_invites, crm_reports, crm_questions, '
                                'employee_invites, manager_memory, deal_events, contracts, source_selections, prompt_active, prompt_versions, prompt_drafts, astra_daily_spend CASCADE')
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

    async def director(self):
        """Вход директора по личной ссылке (не тестовый): только в нём можно что-то менять."""
        token = auth.digest('director-link')
        await self.pool.execute("INSERT INTO crm_sessions(token_hash,expires_at,role,client_id,is_test) VALUES($1,now()+'1 day','director',1,false)",
                                token)
        self.http.session.cookie_jar.update_cookies({'crm_session': 'director-link'})

    async def enter(self, role, **extra):
        r = await self.http.post('/api/test/login', json={'key': TEST_KEY, 'role': role, **extra})
        self.assertEqual(r.status, 403)  # без заголовка X-CRM
        r = await self.http.post('/api/test/login', json={'key': TEST_KEY, 'role': role, **extra}, headers={'X-CRM': '1'})
        self.assertEqual(r.status, 200, await r.text())
        return (await r.json())['go']

    async def test_sources_selections(self):
        """«Источники»: подборки общие для отдела, удалить может автор или руководитель, тестовый вход только смотрит."""
        h = {'X-CRM': '1'}
        await self.login(123)
        sel = {'niche': 'kitchen', 'niche_name': 'Кухни на заказ', 'query': 'кухни', 'city': 'Новороссийск', 'client': 'Кухни Мария',
               'seg': 'bad', 'id': None, 'donors': [
                   {'name': 'Салон «Кухни Юг»', 'phone': '79181234567', 'site': 'kuhni-yug.ru', 'cat': 0, 'checks': [1, 1, 1, 1],
                    'from': '2gis', 'type': 'direct', 'level': 'good', 'label': 'берём', 'signals': ['Звонки', 'СМС']},
                   {'name': 'Циан', 'phone': '78005553535', 'level': 'bad', 'type': 'evil', 'from': 'x'}]}
        r = await self.http.post('/api/sources/selections', json={**sel, 'donors': [{'name': 'пусто'}]}, headers=h)
        self.assertEqual(r.status, 400)
        r = await self.http.post('/api/sources/selections', json=sel, headers=h)
        self.assertEqual(r.status, 200, await r.text())
        sid = (await r.json())['id']
        items = (await (await self.http.get('/api/sources/selections')).json())['items']
        self.assertEqual([(i['id'], i['donors'], i['good'], i['author'], i['can_delete']) for i in items], [(sid, 2, 1, 'Первый', True)])
        one = await (await self.http.get(f'/api/sources/selections/{sid}')).json()
        self.assertEqual((one['seg'], one['donors'][0]['checks'], one['donors'][1]['type'], one['donors'][1]['from']), ('', [True] * 4, 'direct', ''))
        # повторное сохранение автором обновляет ту же подборку
        r = await self.http.post('/api/sources/selections', json={**sel, 'id': sid, 'city': 'Анапа'}, headers=h)
        self.assertEqual((await r.json())['id'], sid)
        # другой менеджер видит, но не удаляет и не переписывает: его сохранение — новая подборка
        await self.http.get('/logout')
        await self.login(124)
        items = (await (await self.http.get('/api/sources/selections')).json())['items']
        self.assertEqual([(i['city'], i['can_delete']) for i in items], [('Анапа', False)])
        self.assertEqual((await self.http.post(f'/api/sources/selections/{sid}/delete', json={}, headers=h)).status, 403)
        r = await self.http.post('/api/sources/selections', json={**sel, 'id': sid}, headers=h)
        self.assertNotEqual((await r.json())['id'], sid)
        # руководитель удаляет любую
        await self.http.get('/logout')
        await self.director()
        self.assertEqual((await self.http.post(f'/api/sources/selections/{sid}/delete', json={}, headers=h)).status, 200)
        self.assertEqual(len((await (await self.http.get('/api/sources/selections')).json())['items']), 1)
        self.assertEqual((await self.http.get(f'/api/sources/selections/{sid}')).status, 400)
        # тестовый вход: смотреть можно, сохранять нельзя
        await self.http.get('/logout')
        with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY}):
            await self.enter('director')
            self.assertEqual((await self.http.get('/api/sources/selections')).status, 200)
            self.assertEqual((await self.http.post('/api/sources/selections', json=sel, headers=h)).status, 403)

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

    async def test_public_site_is_view_only(self):
        h = {'X-CRM': '1'}
        r = await self.http.get('/', allow_redirects=False)
        self.assertEqual(r.status, 200)  # сайт закрыт: без ссылки только страница входа
        self.assertEqual((await self.http.get('/t/open')).status, 404)
        with patch.dict(os.environ, {'CRM_PUBLIC': '1', 'CRM_TEST_KEY': ''}), patch('crm.gsheets.values', fake_values), \
                patch.dict(os.environ, {'KITCHEN_SHEET_ID': 'leads'}):
            r = await self.http.get('/', allow_redirects=False)
            self.assertEqual((r.status, r.headers['Location']), (302, '/t/open'))
            self.assertEqual((await self.http.get('/t/open')).status, 200)
            r = await self.http.post('/api/test/login', json={'key': 'open', 'role': 'director'}, headers=h)
            self.assertEqual(r.status, 200)
            self.assertTrue((await (await self.http.get('/api/me')).json())['test'])
            self.assertEqual((await self.http.get('/api/staff')).status, 200)  # смотреть можно всё
            r = await self.http.post('/api/gs', json={'spreadsheetId': 'leads', 'range': 'Сводка!A1:F80'}, headers=h)
            self.assertEqual(r.status, 200)
            # менять нельзя ничего: ни ссылки, ни сотрудников, ни данные страницы, ни звонки, ни вопросы
            for path, data in [('/api/access', {'role': 'director'}), ('/api/staff', {'role': 'head', 'name': 'X'}),
                               ('/api/staff/unlink', {'person_ext': 'head'}), ('/api/docs', {'path': 'a/b', 'op': 'set', 'data': {}}),
                               ('/api/ask', {'question': 'x'}), ('/api/robot/call', {'phone': '89991112233'}),
                               ('/api/robot/cab/stop', {'phone': '89991112233'}), ('/api/trainings', {'person_ext': '101'})]:
                r = await self.http.post(path, json=data, headers=h)
                self.assertEqual(r.status, 403, path)
                self.assertIn('только для просмотра', (await r.json())['error'])
            self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_invites'), 0)
        # сайт закрыли: /t/open больше не открывается
        self.assertEqual((await self.http.get('/t/open')).status, 404)

    async def test_director_docs(self):
        with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY}):
            await self.director()
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
            await self.director()
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

    async def add_review(self, ext, data, ago, number='79990001122'):
        call_id = await self.pool.fetchval(
            """INSERT INTO calls(client_id,extension,call_started_at,client_number,duration_seconds,direction)
               VALUES(1,$1,now()-$2::text::interval,$3,95,2) RETURNING id""", ext, ago, number)
        category = (data.get('key_moment') or {}).get('category')
        await self.pool.execute(
            """INSERT INTO call_reviews(call_id,client_id,extension,prompt_version,kind,success,chance,category,data)
               VALUES($1,1,$2,'call_review_v2',$3,$4,$5,$6,$7::jsonb)""",
            call_id, ext, data['kind'], data['success'], data['chance'], category, json.dumps(data))
        return call_id

    async def test_person_card(self):
        other = {**LOST, 'key_moment': {**LOST['key_moment'], 'category': 'lost_dmp'}}
        hot = {**LOST, 'chance': 'hot'}
        won = {**WON, 'positives': [{'timecode': '00:01', 'speaker': 'manager', 'quote': 'x', 'why': 'Сразу назвал повод звонка.'}]}
        lost_now = await self.add_review('101', LOST, '1 hour')
        await self.add_review('101', hot, '2 hours')
        await self.add_review('101', won, '3 hours')
        await self.add_review('101', other, '9 days')
        await self.add_review('101', WON, '10 days')
        await self.add_review('101', LOST, '20 days')
        await self.add_review('101', LOST, '40 days')  # старше 30 дней: не считается
        await self.add_review('102', other, '1 hour')
        await self.pool.execute("INSERT INTO calls(client_id,extension,call_started_at,duration_seconds,direction) "
                                "VALUES(1,'101',now()-'1 hour'::interval,0,2)")  # недозвон
        with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY, 'KITCHEN_EXTENSIONS': '102'}):
            await self.director()
            d = await (await self.http.get('/api/staff')).json()
            s = next(p for p in d['items'] if p['ext'] == '101')['stats']
            self.assertEqual((s['week']['talks'], s['week']['success'], s['week']['hot']), (3, 1, 1))
            self.assertEqual((s['prev']['talks'], s['prev']['success']), (2, 1))
            self.assertEqual((s['dials']['dials'], s['dials']['answered']), (4, 3))
            self.assertEqual(s['top'], {'key': 'no_callback_date', 'label': 'не закреплён день перезвона', 'n': 3})
            self.assertIsNone(next(p for p in d['items'] if p['ext'] == 'head')['stats'])

            p = await (await self.http.get('/api/person?person=101')).json()
            self.assertEqual((p['name'], p['manager'], p['kitchen']), ('Первый', True, False))
            self.assertEqual(p['periods']['month']['talks'], 6)
            self.assertEqual(p['periods']['month']['outcomes'], {'callback_date': 2})
            first = p['mistakes'][0]
            self.assertEqual((first['key'], first['month'], first['week'], first['prev']), ('no_callback_date', 3, 2, 0))
            self.assertEqual(first['examples'][0]['id'], lost_now)
            self.assertEqual(p['mistakes'][1]['key'], 'lost_dmp')
            self.assertEqual(p['positives'][0]['why'], 'Сразу назвал повод звонка.')
            self.assertEqual(len(p['calls']), 6)
            self.assertEqual(len(p['days']), 30)
            self.assertEqual(sum(x['talks'] for x in p['days']), 6)
            self.assertIn('access', p)
            self.assertTrue((await (await self.http.get('/api/person?person=102')).json())['kitchen'])
            head = await (await self.http.get('/api/person?person=head')).json()
            self.assertEqual((head['manager'], 'periods' in head, head['access']['can_copy']), (False, False, True))
            self.assertEqual((await self.http.get('/api/person?person=nobody')).status, 400)

            # менеджер видит только свою карточку и без доступов
            await self.http.get('/test-exit')
            await self.enter('manager', ext='102')
            p = await (await self.http.get('/api/person?person=101')).json()
            self.assertEqual((p['ext'], 'access' in p), ('102', False))

    async def test_manager_memory_card(self):
        from review import memory
        lost = [await self.add_review('101', LOST, f'{h} hours') for h in (1, 2, 3, 4)]
        won = await self.add_review('101', {**WON, 'positives': [{'timecode': '00:01', 'speaker': 'manager', 'quote': 'x',
                                                                   'why': 'Сразу назвал повод звонка.'}]}, '5 hours')
        await self.add_review('102', LOST, '1 hour')
        seen = []

        async def fake_call(system, user, max_tokens):
            seen.append(json.loads(user))
            return {'weak_spots': [{'category': 'no_callback_date', 'title': 'Не закрепляет день перезвона',
                                    'pattern': 'Соглашается на «перезвоните позже».', 'advice': 'Спросите: «Завтра в 11 удобно?»',
                                    'examples': [lost[0], 999999, str(lost[1]), lost[2]]},
                                   {'category': 'made_up', 'title': 'Общее', 'examples': []},
                                   {'title': ''}],
                    'strengths': [{'title': 'Сразу называет повод', 'examples': [won]}],
                    'focus': 'Закреплять день и время перезвона.', 'trend': 'Сравнить не с чем.', 'focus_result': 'получилось'}, 0.5

        with patch.dict(os.environ, {'MANAGER_MEMORY': 'on', 'MANAGER_MEMORY_HOUR': '19', 'KITCHEN_EXTENSIONS': '102'}):
            evening = datetime.now(ZoneInfo('Europe/Moscow')).replace(hour=19, minute=30).astimezone(timezone.utc)
            morning = evening.replace(hour=5)
            self.assertEqual(await memory.maybe_update(self.pool, morning, fake_call), 0)  # до 19:00 не трогаем
            self.assertEqual(await memory.maybe_update(self.pool, evening, fake_call), 1)  # Ольга (102) не входит
            self.assertEqual(seen[0]['менеджер'], 'Первый')
            self.assertEqual(seen[0]['цифры']['30 дней']['разговоров'], 5)
            self.assertEqual(len(seen[0]['потерянные разговоры (сначала новые)']), 4)
            self.assertEqual(await memory.maybe_update(self.pool, evening, fake_call), 0)  # на сегодня уже есть
            card = memory.loads((await memory.latest(self.pool, 1, '101'))['card'])
            first = card['weak_spots'][0]
            self.assertEqual(first['examples'], [lost[0], lost[1]])  # чужие ID отброшены, не больше двух
            self.assertEqual(first['counts'], {'month': 4, 'week': 4, 'prev': 0})  # количества считает код
            self.assertEqual((card['weak_spots'][1]['category'], card['weak_spots'][1]['counts']), ('other', None))
            self.assertEqual(len(card['weak_spots']), 2)
            self.assertIsNone(card['focus_result'])  # прошлой карточки не было
            self.assertEqual(await self.pool.fetchval('SELECT sum(spent_units) FROM astra_daily_spend'), 0.5)

            # мало разговоров: модель не зовём
            empty = await memory.update(self.pool, 1, '103', 'Новый', call=AsyncMock(side_effect=AssertionError))
            self.assertEqual((empty['weak_spots'], empty['focus']), ([], None))

            await self.director()
            p = await (await self.http.get('/api/person?person=101')).json()
            self.assertTrue(p['memory_on'])
            mem = p['memory'][0]
            self.assertEqual((mem['talks'], mem['focus']), (5, 'Закреплять день и время перезвона.'))
            self.assertEqual(mem['weak_spots'][0]['examples'][0]['id'], lost[0])
            self.assertTrue(mem['weak_spots'][0]['examples'][0]['at'])

    async def test_deals(self):
        lost = await self.add_review('101', LOST, '3 days', number='79990000001')  # холодный без результата
        await self.add_review('101', WON, '5 days', number='79990000002')  # перезвон на дату
        shown = await self.add_review('101', WON, '9 days', number='79990000003')
        await self.add_review('102', LOST, '1 day', number='79990000004')
        await self.add_review('102', LOST, '2 days', number='79990000005')
        await self.pool.execute("INSERT INTO callback_tasks(client_id,extension,call_id,client_number,kind,due_date,raw) "
                                "VALUES(1,'102',$1,'79990000005','callback',current_date+1,'завтра в 11')",
                                await self.pool.fetchval("SELECT id FROM calls WHERE client_number='79990000005'"))
        pres = {'kind': 'presentation', 'stage_after': 'decision_date', 'temperature': 'warm', 'reviewed': True,
                'key_moment': {'category': 'price_no_value', 'what_happened': 'Цена без расчёта.'},
                'contract': {'company': 'ООО Ромашка', 'missing': ['inn', 'signer']},
                'next_step_advice': 'В пятницу узнать решение и запросить ИНН.', 'rop_line': 'Презентация. Тёплый.'}
        await self.pool.execute(
            """INSERT INTO presentations(client_id,file_id,day,extension,status,call_id,data)
               VALUES(1,'f1',current_date-8,'101','reviewed',$1,$2::jsonb)""",
            await self.pool.fetchval("INSERT INTO calls(client_id,extension,call_started_at,client_number,duration_seconds) "
                                     "VALUES(1,'101',now()-'8 days'::interval,'79990000003',900) RETURNING id"), json.dumps(pres))
        await self.pool.execute("INSERT INTO crm_leads(client_id,extension,number) VALUES(1,'101','79990000001'),(1,'101','79990000009')")

        await self.director()
        d = await (await self.http.get('/api/deals')).json()
        by = {x['phone']: x for x in d['deals']}
        self.assertEqual(set(by), {'9990000001', '9990000002', '9990000003', '9990000004', '9990000005'})
        self.assertEqual((by['9990000001']['status'], by['9990000001']['lost']['reason'], by['9990000001']['lost']['auto']),
                         ('lost', 'не закреплён день перезвона', True))
        self.assertEqual(by['9990000005']['status'], 'active')  # есть открытый перезвон
        self.assertIn('Перезвон', by['9990000005']['advice'])
        p = by['9990000003']
        self.assertEqual((p['stage'], p['company'], p['temperature']), ('presentation', 'ООО Ромашка', 'warm'))
        self.assertEqual((p['advice'], p['missing']), ('В пятницу узнать решение и запросить ИНН.', ['ИНН', 'кто подписывает']))
        self.assertEqual(p['stalled_days'], 8)  # больше 5 дней после презентации
        stages = {s['key']: s for s in d['funnel']['stages']}
        self.assertEqual([stages[k]['reached'] for k in ('base', 'cold', 'presentation', 'contract')], [2, 5, 1, 0])
        self.assertEqual(stages['cold']['lost'], 2)
        self.assertTrue(d['can_pay'])
        one = await (await self.http.get('/api/deals/79990000003')).json()
        self.assertEqual([t['type'] for t in one['timeline']], ['call', 'presentation'])
        self.assertEqual(len((await (await self.http.get('/api/deals?person=102')).json())['deals']), 2)

        post = lambda phone, **b: self.http.post(f'/api/deals/{phone}/event', json=b, headers={'X-CRM': '1'})
        r = await post('79990000003', kind='contract_sent', note='Отправил на почту')
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((await r.json())['stage'], 'contract')
        self.assertEqual((await post('79990000003', kind='paid', amount='')).status, 400)
        paid = await (await post('79990000003', kind='paid', amount='165 000')).json()
        self.assertEqual((paid['status'], paid['stage'], paid['amount']), ('paid', 'paid', 165000.0))
        self.assertEqual((await post('79990000003', kind='lost', reason='other')).status, 400)  # «другое» без пояснения
        self.assertEqual((await post('79990000002', kind='lost', reason='expensive')).status, 200)
        d = await (await self.http.get('/api/deals')).json()
        by = {x['phone']: x for x in d['deals']}
        self.assertEqual((by['9990000002']['lost']['reason'], by['9990000002']['lost']['auto']), ('дорого', False))
        self.assertEqual((d['funnel']['paid'], d['funnel']['paid_sum']), (1, 165000.0))
        reopened = await (await post('79990000002', kind='reopen')).json()
        self.assertEqual(reopened['status'], 'active')  # после «снова в работе» авто-отказ не возвращается

        # презентации не через Mango: без клиента, пока номер не укажут вручную
        for file_id, ext in (('m1', '101'), ('m2', '101'), ('m3', '102')):
            await self.pool.execute(
                """INSERT INTO presentations(client_id,file_id,day,time_text,extension,status,data)
                   VALUES(1,$1,current_date-1,'09:30',$2,'reviewed',$3::jsonb)""", file_id, ext, json.dumps(pres))
        ids = {r['file_id']: r['id'] for r in await self.pool.fetch("SELECT id, file_id FROM presentations WHERE file_id LIKE 'm%'")}
        d = await (await self.http.get('/api/deals')).json()
        self.assertEqual({u['file_id'] for u in d['unlinked']}, {'m1', 'm2', 'm3'})
        self.assertTrue(d['unlinked'][0]['at'].endswith('12:30'))  # время реестра по UTC, показываем по Москве
        link = lambda pid, phone: self.http.post(f'/api/presentations/{pid}/link', json={'phone': phone}, headers={'X-CRM': '1'})
        self.assertEqual((await link(ids['m1'], '@nick')).status, 400)
        r = await link(ids['m1'], '+7 (999) 000-00-01')  # был холодный звонок с этим номером
        self.assertEqual((r.status, await r.json()), (200, {'phone': '9990000001', 'call_found': True}))
        r = await link(ids['m2'], '8 912 345-67-89')  # номера в Mango нет: сделка по самой презентации
        self.assertEqual(await r.json(), {'phone': '9123456789', 'call_found': False})
        d = await (await self.http.get('/api/deals')).json()
        by = {x['phone']: x for x in d['deals']}
        self.assertEqual({u['file_id'] for u in d['unlinked']}, {'m3'})
        self.assertEqual((by['9990000001']['stage'], by['9990000001']['status']), ('presentation', 'active'))
        self.assertEqual((by['9123456789']['stage'], by['9123456789']['ext']), ('presentation', '101'))
        timeline = (await (await self.http.get('/api/deals/79990000001')).json())['timeline']
        self.assertEqual([t['type'] for t in timeline][-1], 'presentation')
        self.assertEqual(await self.pool.fetchval("SELECT linked_by FROM presentations WHERE file_id='m1'"), 'Директор')

        # менеджер: только свои сделки, без оплаты
        self.http.session.cookie_jar.clear()
        await self.login(123)
        mine = await (await self.http.get('/api/deals')).json()
        self.assertEqual({x['ext'] for x in mine['deals']}, {'101'})
        self.assertFalse(mine['can_pay'])
        self.assertEqual((await self.http.get('/api/deals/79990000004')).status, 403)
        self.assertEqual((await post('79990000002', kind='paid', amount='1000')).status, 403)
        self.assertEqual((await post('79990000004', kind='note', note='x')).status, 403)
        self.assertEqual((await post('79990000002', kind='materials', note='КП в Telegram')).status, 200)
        self.assertEqual([u['file_id'] for u in mine['unlinked']], [])  # m3 — презентация другого менеджера
        self.assertEqual((await link(ids['m3'], '+7 999 111-22-33')).status, 403)

    async def test_contracts(self):
        import asyncio
        from crm import contracts
        await self.add_review('101', WON, '2 days', number='79990000001')
        await self.add_review('102', WON, '2 days', number='79990000002')
        h = {'X-CRM': '1'}
        reg = lambda **b: self.http.post('/api/contracts/register', json=b, headers=h)

        await self.director()
        cfg = await (await self.http.get('/api/contracts/config')).json()
        self.assertEqual(sorted(cfg['templates']), ['ds', 'gck', 'kc', 'ops'])
        self.assertFalse(cfg['innLookup'])  # без DADATA_KEY поиска по ИНН нет
        self.assertEqual((await (await self.http.get('/api/contracts/next-number')).json())['num'], 1)
        r = await reg(type='ops', date='2026-10-20', client='ООО «Ромашка»', inn='7707083893', sum=37000, tariff='Старт')
        self.assertEqual((r.status, (await r.json())['num']), (200, 1))
        self.assertEqual((await (await reg(type='ds', client='ООО «Ромашка»', sum='25 000', baseNum='1')).json())['num'], '')
        self.assertEqual((await reg(type='xx')).status, 400)
        # номера не повторяются, даже если оформляют одновременно
        got = await asyncio.gather(*(reg(type='gck', sum=70000) for _ in range(5)))
        self.assertEqual(sorted([(await g.json())['num'] for g in got]), [2, 3, 4, 5, 6])
        self.assertEqual((await (await self.http.get('/api/contracts/next-number')).json())['num'], 7)
        log = (await (await self.http.get('/api/contracts')).json())['items']
        self.assertEqual(len(log), 7)
        ds = next(c for c in log if c['type'] == 'ds')
        self.assertEqual((ds['num'], ds['amount'], ds['base_num'], ds['by']), (None, 25000.0, '1', 'Директор'))

        # из карточки сделки: договор виден в сделке, чужую сделку менеджер не трогает
        self.http.session.cookie_jar.clear()
        await self.login(123)
        r = await reg(type='kc', client='ИП Иванов', sum=25000, deal='+7 999 000-00-01')
        self.assertEqual((r.status, (await r.json())['num']), (200, 7))
        self.assertEqual((await reg(type='kc', deal='79990000002')).status, 403)
        deal = await (await self.http.get('/api/deals/79990000001')).json()
        self.assertEqual([(c['num'], c['type'], c['by']) for c in deal['contracts']], [(7, 'kc', 'Первый')])
        self.assertEqual([c['num'] for c in (await (await self.http.get('/api/contracts')).json())['items']], [7])

        self.assertEqual((await self.http.get('/api/contracts/lookup/inn/123')).status, 400)
        self.assertEqual((await self.http.get('/api/contracts/lookup/inn/7707083893')).status, 403)  # ключа нет
        answer = type('R', (), {'status_code': 200, 'json': lambda self: {'suggestions': [{'value': 'ПАО СБЕРБАНК'}]}})()
        with patch.dict(os.environ, {'DADATA_KEY': 'k1'}), patch('httpx.AsyncClient.post', AsyncMock(return_value=answer)) as post:
            self.assertTrue((await (await self.http.get('/api/contracts/config')).json())['innLookup'])
            got = await (await self.http.get('/api/contracts/lookup/inn/7707083893')).json()
            self.assertEqual(got, {'ok': True, 'raw': {'value': 'ПАО СБЕРБАНК'}})
            self.assertEqual(post.call_args.args[0], contracts.DADATA_URL + 'party')
            self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Token k1')

        # тестовый вход только смотрит
        self.http.session.cookie_jar.clear()
        with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY}):
            await self.enter('manager', ext='101')
        self.assertEqual((await reg(type='ops')).status, 403)

    async def test_staff_invite_unlink_and_copies(self):
        import invites
        h = {'X-CRM': '1'}
        await self.pool.execute("INSERT INTO employees(client_id,extension,telegram_user_id,role,full_name) VALUES(1,'own',900,'owner','Владелец')")
        with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY, 'BOT_USERNAME': 'averon_bot', 'REVIEW_COPY_TO': '900, 555'}):
            await self.enter('manager', ext='101')
            self.assertEqual((await self.http.get('/api/staff')).status, 403)
            await self.http.get('/test-exit')
            await self.director()
            d = await (await self.http.get('/api/staff')).json()
            self.assertEqual([p['ext'] for p in d['items']], ['own', 'head', '101', '102'])
            self.assertEqual((d['copies_source'], d['copies_other']), ('env', 1))
            own = d['items'][0]
            self.assertEqual((own['copies'], own['can_unlink'], own['can_copy']), (True, False, True))
            # новый менеджер: строка и одноразовая ссылка в бота, по ней он подключается сам
            r = await self.http.post('/api/staff', json={'role': 'manager', 'person_ext': '15', 'name': 'Пётр'}, headers=h)
            link = (await r.json())['link']
            self.assertTrue(link.startswith('https://t.me/averon_bot?start=inv_'), link)
            row, why = await invites.accept(self.pool, link.rsplit('inv_', 1)[1], 555, 'Пётр Иванов')
            self.assertEqual((row['extension'], row['role'], why), ('15', 'manager', None))
            r = await self.http.post('/api/staff', json={'role': 'manager', 'person_ext': '15', 'name': 'Другой'}, headers=h)
            self.assertEqual(r.status, 400)  # место занято подключённым человеком
            r = await self.http.post('/api/staff', json={'role': 'manager', 'person_ext': 'own', 'name': 'X'}, headers=h)
            self.assertEqual(r.status, 400)
            # руководитель: head:<латиница>, ник сохраняется
            r = await self.http.post('/api/staff', json={'role': 'head', 'name': 'Иван', 'username': '@Pivon_dopola'}, headers=h)
            self.assertEqual(r.status, 200, await r.text())
            self.assertEqual(await self.pool.fetchval("SELECT telegram_username FROM employees WHERE extension='head:ivan'"),
                             'pivon_dopola')
            # отключить: владельца нельзя, руководителя можно, его приглашения гаснут
            self.assertEqual((await self.http.post('/api/staff/unlink', json={'person_ext': 'own'}, headers=h)).status, 403)
            r = await self.http.post('/api/staff/invite', json={'person_ext': 'head'}, headers=h)
            self.assertEqual(r.status, 400)  # уже подключён
            self.assertEqual((await self.http.post('/api/staff/unlink', json={'person_ext': 'head'}, headers=h)).status, 200)
            self.assertIsNone(await self.pool.fetchval("SELECT telegram_user_id FROM employees WHERE extension='head'"))
            r = await self.http.post('/api/staff/invite', json={'person_ext': 'head'}, headers=h)
            code = (await r.json())['link'].rsplit('inv_', 1)[1]
            await self.http.post('/api/staff/unlink', json={'person_ext': 'head'}, headers=h)  # не подключён: 400
            await self.pool.execute("UPDATE employees SET telegram_user_id=902 WHERE extension='head'")
            await self.http.post('/api/staff/unlink', json={'person_ext': 'head'}, headers=h)
            self.assertEqual((await invites.accept(self.pool, code, 777, 'x'))[0], None)
            # копии разборов: менеджеру нельзя; список переезжает в базу и сразу действует
            r = await self.http.post('/api/staff/copies', json={'person_ext': '101', 'on': True}, headers=h)
            self.assertEqual(r.status, 400)
            await self.pool.execute("UPDATE employees SET telegram_user_id=903 WHERE extension='head:ivan'")
            r = await self.http.post('/api/staff/copies', json={'person_ext': 'head:ivan', 'on': True}, headers=h)
            self.assertEqual(r.status, 200)
            self.assertEqual(await review_store.copy_chat_ids(self.pool, 1), [900, 555, 903])
            await self.http.post('/api/staff/copies', json={'person_ext': 'own', 'on': False}, headers=h)
            self.assertEqual(await review_store.copy_chat_ids(self.pool, 1), [555, 903])
            d = await (await self.http.get('/api/staff')).json()
            self.assertEqual(d['copies_source'], 'crm')
            self.assertEqual(next(p['copies'] for p in d['items'] if p['ext'] == 'head:ivan'), True)

    async def test_director_link_bound_to_telegram_and_prompts(self):
        from review import prompts
        h = {'X-CRM': '1'}
        with patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY, 'PROMPT_EDITORS': '901, 905'}):
            await self.director()
            r = await self.http.get('/api/prompts')
            self.assertEqual(r.status, 403)  # тестовый вход без Telegram
            self.assertIn('Доступах', (await r.json())['error'])
            access = await (await self.http.get('/api/access')).json()
            self.assertEqual(access['directors'], [{'tg': '901', 'name': 'Руководитель'}])
            r = await self.http.post('/api/access', json={'role': 'director', 'tg': '124'}, headers=h)
            self.assertEqual(r.status, 400)  # менеджер, не руководитель
            r = await self.http.post('/api/access', json={'role': 'director', 'tg': '901'}, headers=h)
            token = (await r.json())['link'].rsplit('/', 1)[1]
            await self.http.get('/test-exit')
        with patch.dict(os.environ, {'PROMPT_EDITORS': '901, 905'}), \
                patch('crm.telegram.send', AsyncMock(return_value=True)) as send:
            r = await self.http.get('/p/' + token, allow_redirects=False)
            self.assertEqual(r.headers['Location'], '/director')
            me = await (await self.http.get('/api/me')).json()
            self.assertEqual((me['role'], me['name']), ('director', 'Руководитель'))
            o = await (await self.http.get('/api/prompts')).json()
            self.assertEqual([k['kind'] for k in o['kinds']], list(prompts.KINDS))
            st = await (await self.http.get('/api/prompts/morning')).json()
            self.assertEqual((st['body'], st['source']), (prompts.DIGESTS['morning'], 'исходная версия'))
            new = 'Три темы на планёрку за {start}–{end}, коротко.'
            r = await self.http.post('/api/prompts/morning/save', json={'body': new, 'scope': 'all', 'token': 'x'}, headers=h)
            self.assertEqual(r.status, 400)  # без примера не сохраняется
            with patch('review.prompt_ui.preview', AsyncMock(return_value=('<b>Итог</b> <a href="x">ок</a>', None, None))):
                p = await (await self.http.post('/api/prompts/morning/preview', json={'body': new}, headers=h)).json()
            self.assertEqual(p['html'], '<b>Итог</b> ок')
            r = await self.http.post('/api/prompts/morning/save', json={'body': new + ' ', 'scope': 'all', 'token': p['token']},
                                     headers=h)
            self.assertEqual(r.status, 200)  # пробелы по краям не меняют текст
            r = await self.http.post('/api/prompts/morning/save', json={'body': new + '!', 'scope': 'all', 'token': p['token']},
                                     headers=h)
            self.assertEqual(r.status, 400)  # другой текст — нужен новый пример
            self.assertEqual((await prompts.common(self.pool, 'morning'))[0], new)
            send.assert_awaited_once()
            self.assertEqual(send.await_args.args[0], 905)
            st = await (await self.http.get('/api/prompts/morning')).json()
            self.assertEqual(st['source'], 'общая версия')
            original = next(v['id'] for v in st['versions'] if v['note'] == prompts.ORIGINAL_NOTE)
            r = await self.http.post(f'/api/prompts/version/{original}/use', json={'scope': 'me'}, headers=h)
            self.assertEqual(r.status, 200)
            self.assertEqual((await prompts.resolve(self.pool, 'morning', 901))[1], 'personal')
            await self.http.post('/api/prompts/morning/personal-off', json={}, headers=h)
            self.assertEqual((await prompts.resolve(self.pool, 'morning', 901))[0], new)
            # руководителя отключили от бота — его ссылка директора больше не открывается
            await self.pool.execute("UPDATE employees SET telegram_user_id=NULL WHERE extension='head'")
            self.assertEqual((await self.http.get('/api/me')).status, 401)

    async def test_robot_page(self):
        import tempfile
        from aiohttp import web as aweb
        from aiohttp.test_utils import TestServer
        from crm import robot
        from voice import speech
        h = {'X-CRM': '1'}
        dialed = []

        async def fake_dial(request):
            dialed.append(await request.json())
            return aweb.json_response({'id': 'abc'})
        fake = aweb.Application()
        fake.router.add_post('/dial', fake_dial)
        server = TestServer(fake)
        await server.start_server()
        tmp = Path(tempfile.mkdtemp())
        log = tmp / 'calls.jsonl'
        log.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in [
            {'at': '2026-10-17T10:00:00+03:00', 'phone': '79991112233', 'mode': 'телефон', 'outcome': 'interested',
             'comment': 'Нужна кухня 3 м', 'facts': {'город': 'Краснодар'}, 'duration_s': 75,
             'transcript': [['robot', 'Здравствуйте'], ['client', 'Да, актуально']]},
            {'at': '2026-10-17T10:05:00+03:00', 'phone': '', 'mode': 'браузер', 'outcome': 'callback', 'transcript': []}]) + '\n')
        robot._last_dial['at'] = 0.0
        try:
            with patch.object(robot, 'DATA', tmp), patch.object(robot, 'CALLS_LOG', log), \
                    patch.object(speech, 'SETTINGS_FILE', str(tmp / 'voice-settings.json')), \
                    patch.object(robot, 'api_port', lambda: server.port), \
                    patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY, 'ELEVENLABS_API_KEY': ''}):
                await self.enter('manager', ext='101')
                self.assertEqual((await self.http.get('/api/robot')).status, 403)
                await self.http.get('/test-exit')
                token = await auth.invite_create(self.pool, 1, 'director', tg='901')
                await self.http.get('/p/' + token)
                d = await (await self.http.get('/api/robot')).json()
                self.assertTrue(d['up'])
                self.assertIn('kitchen', [o['id'] for o in d['operators']])
                self.assertIn('stability', d['settings'])
                r = await self.http.post('/api/robot/settings', json={'voice_id': 'Voice1234567890', 'stability': 0.7, 'speed': 5, 'junk': 1},
                                         headers=h)
                self.assertEqual((await r.json())['saved'], {'voice_id': 'Voice1234567890', 'stability': 0.7, 'speed': 1.2})
                self.assertEqual(json.loads((tmp / 'voice-settings.json').read_text())['voice_id'], 'Voice1234567890')
                r = await self.http.post('/api/robot/call', json={'phone': '12345', 'operator': 'kitchen'}, headers=h)
                self.assertEqual(r.status, 400)
                r = await self.http.post('/api/robot/call', json={'phone': '8 999 111-22-33', 'operator': 'kitchen'}, headers=h)
                self.assertEqual((await r.json())['number'], '79991112233')
                self.assertEqual(dialed, [{'phone': '79991112233', 'operator': 'kitchen'}])
                r = await self.http.post('/api/robot/call', json={'phone': '89991112233', 'operator': 'kitchen'}, headers=h)
                self.assertEqual(r.status, 400)  # второй звонок сразу не уходит
                calls = (await (await self.http.get('/api/robot/calls')).json())['items']
                self.assertEqual(len(calls), 1)  # браузерный тест не показываем
                self.assertEqual((calls[0]['outcome'], calls[0]['transcript'][0]['who']), ('Заинтересованный', 'Робот'))
                with patch('voice.speech.synthesize_pcm', AsyncMock(return_value=b'\x00\x01' * 10)) as synth:
                    r = await self.http.post('/api/robot/preview', json={'text': 'Привет', 'settings': {'voice_id': 'Voice2234567890', 'speed': 1.1}},
                                             headers=h)
                    self.assertEqual((r.status, len(await r.read())), (200, 20))
                    self.assertEqual(synth.await_args.args[1].name, '11:Voice2234567890')
        finally:
            await server.close()

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

