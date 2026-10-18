"""Перезвоны, Google-таблица и тренировка по разбору. Данные вымышленные."""
import copy
import json
import os
import unittest
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import asyncpg
import httpx

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')

from review import callbacks, engine, render, sheets, store
from tests.test_review import CALLBACK, LOST, TRANSCRIPT, WON
from tools import Actor, Forbidden

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')
MSK = ZoneInfo('Europe/Moscow')
MANAGER = Actor(123, 1, 'manager', '101')
TIMED = {**WON, 'client': {'company': 'ООО Ромашка', 'need': 'больше заявок', 'objection': 'дорого',
                           'next_step': {'type': 'callback', 'date': '2026-10-09', 'time': '11:00',
                                         'date_is_estimate': False, 'raw': 'созвон в пятницу в 11'}}}


class PureTests(unittest.TestCase):
    def test_step_needs_date_and_parses_time(self):
        self.assertIsNone(callbacks.step({'reviewed': True, 'client': {'next_step': {'date': None}}}))
        found = callbacks.step({'reviewed': True, **TIMED})
        self.assertEqual((found['kind'], found['due_date'], found['due_time']), ('callback', date(2026, 10, 9), time(11, 0)))
        self.assertEqual(callbacks.step({'reviewed': True, 'outcome': ['meeting'], **{'client': TIMED['client']}})['kind'], 'meeting')
        self.assertIsNone(callbacks.parse_time('после обеда'))

    def test_next_workday_skips_weekend(self):
        self.assertEqual(callbacks.next_workday(date(2026, 10, 9)), date(2026, 10, 12))

    def test_train_button_only_with_trainer_bot(self):
        with patch.dict(os.environ, {'TRAIN_BOT_USERNAME': ''}):
            self.assertEqual(len(render.keyboard(5, train=True).inline_keyboard[0]), 1)
        with patch.dict(os.environ, {'TRAIN_BOT_USERNAME': '@trainer_bot'}):
            row = render.keyboard(5, train=True).inline_keyboard[0]
            self.assertEqual([b.callback_data for b in row], ['rv:d:5', 'rv:t:5'])
            self.assertEqual(len(render.keyboard(5).inline_keyboard[0]), 1)  # РОПу без тренировки


class SheetsWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_writes_styled_sheets_and_drops_stale_tab(self):
        seen = []

        def handler(request):
            body = json.loads(request.content) if request.content else None
            seen.append((request.method, request.url.path, body))
            self.assertEqual(request.headers['Authorization'], 'Bearer tok')
            if request.method == 'GET':
                return httpx.Response(200, json={'sheets': [
                    {'properties': {'sheetId': 5, 'title': 'Звонки', 'gridProperties': {'rowCount': 3, 'columnCount': 5}},
                     'conditionalFormats': [{}, {}]},
                    {'properties': {'sheetId': 6, 'title': 'Клиенты', 'gridProperties': {'rowCount': 1000, 'columnCount': 26}}}]})
            if request.url.path.endswith(':batchUpdate') and 'addSheet' in body['requests'][0]:
                return httpx.Response(200, json={'replies': [{'addSheet': {'properties': {'sheetId': 9}}}]})
            return httpx.Response(200, json={})

        calls = sheets.Table('Звонки', [100, 100], frozen=1, filter_from=0, chip_column=1)
        calls.add(['Дата', 'Итог'], 'header')
        calls.add(['Звонки за 07.10.2026 · 2'], 'section')
        calls.add(['07.10.2026 11:40', 'Результат'])
        calls.add(['07.10.2026 11:00', 'Без шанса'])
        summary = sheets.Table('Сводка', [200])
        summary.add(['Итог'], 'title')
        with patch.dict(os.environ, {'GOOGLE_SHEET_ID': 'sheet1'}):
            await sheets.write([summary, calls], http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                               access_token='tok')
        batches = [body['requests'] for method, path, body in seen if path.endswith(':batchUpdate')]
        self.assertIn('addSheet', batches[0][0])  # «Сводка» создана
        self.assertEqual(batches[1][0]['appendDimension']['dimension'], 'ROWS')  # в «Звонках» не хватало строк
        self.assertEqual(batches[2], [{'deleteSheet': {'sheetId': 6}}])
        puts = [(path, body) for method, path, body in seen if method == 'PUT']
        self.assertEqual(puts[1][1]['values'][2], ['07.10.2026 11:40', 'Результат'])
        unmerge = batches[3]  # объединения снимаются до записи значений, иначе строка на месте разделителя теряет ячейки
        self.assertEqual({r['unmergeCells']['range']['sheetId'] for r in unmerge}, {5, 9})
        self.assertLess(seen.index(next(x for x in seen if x[2] is not None and x[2].get('requests') == unmerge)),
                        seen.index(next(x for x in seen if x[0] == 'PUT')))
        styling = batches[4]
        self.assertEqual(sum('deleteConditionalFormatRule' in r for r in styling), 2)
        merges = [r['mergeCells']['range'] for r in styling if 'mergeCells' in r]
        self.assertEqual([(m['sheetId'], m['startRowIndex']) for m in merges], [(5, 1)])
        header = next(r['repeatCell'] for r in styling if 'repeatCell' in r and r['repeatCell']['range']['sheetId'] == 5
                      and r['repeatCell']['range'].get('endRowIndex') == 1 and 'backgroundColor' in r['repeatCell']['cell']['userEnteredFormat']
                      and r['repeatCell']['cell']['userEnteredFormat']['textFormat']['bold'])
        self.assertAlmostEqual(header['cell']['userEnteredFormat']['backgroundColor']['red'], 0x1F / 255)
        self.assertTrue(any('setBasicFilter' in r for r in styling))

    def test_enabled_needs_both_settings(self):
        with patch.dict(os.environ, {'GOOGLE_SHEET_ID': 'x', 'GOOGLE_SERVICE_ACCOUNT_FILE': ''}):
            self.assertFalse(sheets.enabled())


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class CallbackStoreTests(unittest.IsolatedAsyncioTestCase):
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
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS id BIGSERIAL;
            ALTER TABLE pending_train_assignments ADD COLUMN IF NOT EXISTS topic TEXT;
            ALTER TABLE pending_train_assignments ADD COLUMN IF NOT EXISTS mode TEXT;
            ALTER TABLE pending_train_assignments ADD COLUMN IF NOT EXISTS assigned_by_telegram_user_id BIGINT;""")
        for name in ('008_call_reviews.sql', '009_callback_tasks.sql'):
            await self.pool.execute((root / 'migrations' / name).read_text())
        await self.pool.execute('TRUNCATE clients CASCADE')
        await self.pool.execute("INSERT INTO clients VALUES(1,'Europe/Moscow',true)")
        await self.pool.execute("INSERT INTO employees VALUES(1,'101',123,'manager','Первый'),(1,'102',124,'manager','Второй'),(1,'head',901,'head','Руководитель')")
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
        self.client = await self.pool.fetchrow('SELECT * FROM clients WHERE id=1')

    async def asyncTearDown(self):
        await self.pool.close()

    async def add(self, data, transcript=CALLBACK, number='79990001122'):
        call_id = await self.pool.fetchval(
            "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number) VALUES(1,'101',$1,$2,$3) RETURNING id",
            transcript, datetime(2026, 10, 7, 11, 40, tzinfo=MSK), number)
        call = await self.pool.fetchrow('SELECT * FROM calls WHERE id=$1', call_id)
        review = engine.validate(copy.deepcopy(data), transcript)
        async with self.pool.acquire() as conn:
            await store.save(conn, call, review, 1.0)
            task_id = await callbacks.record(conn, call, review)
        return call_id, task_id

    async def test_new_call_closes_open_task_and_creates_next(self):
        first, task_id = await self.add(TIMED)
        task = await self.pool.fetchrow('SELECT * FROM callback_tasks WHERE id=$1', task_id)
        self.assertEqual((task['due_date'], task['due_time'], task['company']), (date(2026, 10, 9), time(11, 0), 'ООО Ромашка'))
        second, second_task = await self.add(LOST, TRANSCRIPT)
        closed = await self.pool.fetchrow('SELECT status, closed_call_id FROM callback_tasks WHERE id=$1', task_id)
        self.assertEqual(tuple(closed), ('closed_by_call', second))
        self.assertEqual(await self.pool.fetchval('SELECT due_date FROM callback_tasks WHERE id=$1', second_task), date(2026, 11, 9))
        not_reviewed = {**LOST, 'kind': 'no_conversation'}
        _, none = await self.add(not_reviewed, TRANSCRIPT)
        self.assertIsNone(none)
        self.assertEqual(await self.pool.fetchval("SELECT status FROM callback_tasks WHERE id=$1", second_task), 'open')

    async def test_morning_list_and_reminder(self):
        _, task_id = await self.add(TIMED)
        await self.add(TIMED, number='79990003344')
        morning = datetime(2026, 10, 9, 9, 5, tzinfo=MSK)
        self.assertEqual(await callbacks.send_morning(self.pool, self.bot, self.client, morning), 1)
        chat, text = self.bot.send_message.await_args.args
        self.assertEqual(chat, 123)
        self.assertIn('Перезвоны на сегодня', text)
        self.assertIn('2. ', text)
        self.assertEqual(len(self.bot.send_message.await_args.kwargs['reply_markup'].inline_keyboard), 2)
        early = datetime(2026, 10, 9, 10, 0, tzinfo=MSK)
        self.assertEqual(await callbacks.send_reminders(self.pool, self.bot, self.client, early), 0)
        soon = datetime(2026, 10, 9, 10, 40, tzinfo=MSK)
        self.assertEqual(await callbacks.send_reminders(self.pool, self.bot, self.client, soon), 2)
        self.assertEqual(await callbacks.send_reminders(self.pool, self.bot, self.client, soon), 0)

    async def test_buttons_change_status_only_for_own_tasks(self):
        _, task_id = await self.add(TIMED)
        with self.assertRaises(Forbidden):
            await callbacks.change(self.pool, Actor(124, 1, 'manager', '102'), task_id, 'done')
        note = await callbacks.change(self.pool, MANAGER, task_id, 'moved')
        self.assertTrue(note.startswith('Перенесён на'))
        self.assertGreater(await self.pool.fetchval('SELECT due_date FROM callback_tasks WHERE id=$1', task_id), date(2026, 10, 9))
        self.assertEqual(await callbacks.change(self.pool, Actor(901, 1, 'head', None), task_id, 'done'), 'Отмечен как выполненный.')
        self.assertEqual(await callbacks.change(self.pool, MANAGER, task_id, 'cancelled'), 'Уже выполнен.')

    async def test_sheet_rows_from_database(self):
        await self.add(TIMED)
        await self.add(LOST, TRANSCRIPT, number='79990003344')
        summary, days, calls, tasks, everything = await sheets.collect(self.pool, datetime(2026, 10, 7, 12, tzinfo=MSK))
        self.assertEqual([t.title for t in (summary, days, calls, tasks, everything)],
                         ['Сводка', 'По дням', 'Звонки', 'Перезвоны', 'Все звонки'])
        self.assertEqual(calls.rows[0], sheets.CALLS_HEADER)
        self.assertEqual((calls.styles[1], calls.rows[1]), ('section', ['Звонки за 07.10.2026 · 2']))
        won = next(r for r in calls.rows[2:] if r[3] == 'ООО Ромашка')
        self.assertEqual([won[0], won[1], won[4]], ['07.10.2026 11:40', 'Первый', 'Результат'])
        self.assertEqual(next(r for r in calls.rows[2:] if r[3] == '')[4], 'Потерян, шанс был')
        self.assertEqual(days.rows[1][:3], ['07.10.2026', 2, 1])
        total = summary.rows[summary.styles.index('total')]
        self.assertEqual(total[:4], ['ИТОГО', 2, 1, '50%'])
        self.assertIn(['не закреплён день перезвона', 1], summary.rows)
        self.assertEqual([r[0] for r, s in zip(tasks.rows, tasks.styles) if s == 'section'], ['Дальше · 2'])
        self.assertEqual(tasks.rows[2][7], 'открыт')

    async def test_all_calls_sheet_counts_every_mango_call(self):
        await self.add(TIMED)  # разобранный разговор 90 с
        day = datetime(2026, 10, 7, 10, 0, tzinfo=MSK)
        await self.pool.execute(
            """INSERT INTO calls(client_id,extension,direction,duration_seconds,call_started_at,status) VALUES
               (1,'101',2,0,$1,'skipped'),(1,'101',2,12,$1,'skipped'),(1,'102',2,0,$1,'skipped'),
               (1,'101',1,40,$1,'skipped'),(1,'101',1,0,$1,'skipped'),(1,NULL,1,0,$1,'skipped'),
               (1,'101',3,50,$1,'skipped'),(1,'101',2,60,$2,'skipped')""", day, day.replace(day=6))
        *_, everything = await sheets.collect(self.pool, datetime(2026, 10, 7, 12, tzinfo=MSK))
        self.assertEqual(everything.rows[0], sheets.all_calls_header(30))
        rows = {(r[0], r[1]): r for r in everything.rows[1:]}
        # исходящих 3 (0, 12 и 90 с), дозвонились 2, разговоров от 30 с 1; входящих 2, пропущен 1; внутренний не считается
        self.assertEqual(rows[('07.10.2026', 'Первый')][2:], ['101', 3, 2, 1, 2, 1, round((12 + 90 + 40) / 60), 1])
        self.assertEqual(rows[('07.10.2026', 'Второй')][3:6], [1, 0, 0])
        self.assertEqual(rows[('07.10.2026', 'Без сотрудника')][6:8], [1, 1])
        self.assertEqual(rows[('06.10.2026', 'Первый')][3:6], [1, 1, 1])
        self.assertEqual(everything.rows[1][0], '07.10.2026')
        self.assertEqual(everything.rows[1][1], 'Первый')  # внутри дня больше всех исходящих выше

    async def test_train_assignment_and_scenario(self):
        import training_simulator
        call_id, _ = await self.add({**LOST, 'client': {**LOST['client'], 'company': 'ООО Ромашка', 'objection': 'занят'}}, TRANSCRIPT)
        with self.assertRaises(Forbidden):
            await store.train_assignment(self.pool, Actor(124, 1, 'manager', '102'), call_id)
        with self.assertRaises(Forbidden):
            await store.train_assignment(self.pool, Actor(901, 1, 'head', None), call_id)
        assignment = await store.train_assignment(self.pool, MANAGER, call_id)
        topic = await self.pool.fetchval('SELECT topic FROM pending_train_assignments WHERE id=$1', assignment)
        self.assertEqual(topic, f'review:{call_id}')
        kind, scenario = await training_simulator.pick_scenario(self.pool, 1, '101', topic)
        self.assertEqual(kind, 'review')
        self.assertIn('ООО Ромашка', scenario['extra'])
        self.assertIn('«наберите через месяц»', scenario['extra'])
        self.assertIsNone(await training_simulator.review_scenario(self.pool, 2, topic))  # чужой клиент


if __name__ == '__main__':
    unittest.main()
