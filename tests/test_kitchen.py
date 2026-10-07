"""Прозвон лидов по скрипту кухонь: итог попытки и запись в таблицу. Данные вымышленные."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

import asyncpg
import httpx

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')

from review import kitchen

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')
TALK = '[00:01 Спикер 0] Добрый день, кухни на заказ.\n[00:04 Спикер 1] Да, думаем в этом месяце.'
READY = {'status': 'interested', 'last_step': 6, 'comment': 'Казань, плёнка, 300 тыс., Ирина Петровна, завтра после 18',
         'facts': {'timeline': 'да', 'city': 'Казань', 'material': 'плёнка', 'film_ok': True, 'budget': '300 тысяч',
                   'budget_ok': True, 'name': 'Ирина Петровна', 'callback_time': 'завтра после 18'}}
GRID = [
    ['id', 'Проект', 'Тег проекта', 'Телефон', 'Создано'],
    ['База от 30.09.2026'],
    ['1801', 'B3_кухонная гарнитура', '', '79990001122', '30.09.2026 11:26'],
    ['База от 02.10.2026'],
    ['1802', 'B3_кухонная гарнитура', '', '79990003344', '02.10.2026 10:00'],
]
SHEETS = {'sheets': [{'properties': {'sheetId': 0, 'title': 'Сводка', 'gridProperties': {'rowCount': 40, 'columnCount': 6}}},
                     {'properties': {'sheetId': 1, 'title': 'По дням', 'gridProperties': {'rowCount': 40, 'columnCount': 7}}},
                     {'properties': {'sheetId': 2, 'title': 'Кухонные гарнитуры', 'gridProperties': {'rowCount': 300, 'columnCount': 16}}}]}
UTC = timezone.utc
MSK = ZoneInfo('Europe/Moscow')


def attempt(call_id, status, number='79990001122', comment='коммент', minutes=0, facts=None):
    return {'call_id': call_id, 'status': status, 'client_number': number, 'comment': comment,
            'call_started_at': datetime(2026, 10, 7, 9, tzinfo=UTC) + timedelta(minutes=minutes),
            'data': json.dumps({'facts': facts or {}}, ensure_ascii=False)}


class DecideTests(unittest.TestCase):
    def test_full_checklist_stays_interested(self):
        with patch.dict(os.environ, {'KITCHEN_REGIONS': 'Казань, Татарстан'}):
            self.assertEqual(kitchen.decide(dict(READY), TALK)['status'], 'interested')

    def test_missing_items_downgrade_to_callback(self):
        data = json.loads(json.dumps(READY))
        data['facts']['budget_ok'] = None
        data['facts']['name'] = None
        out = kitchen.decide(data, TALK)
        self.assertEqual(out['status'], 'callback')
        self.assertTrue(out['comment'].startswith('Проверь: не хватает бюджет от 250 тыс., имя.'))

    def test_region_outside_list_is_not_paid_as_interested(self):
        with patch.dict(os.environ, {'KITCHEN_REGIONS': 'Москва;Московская область'}):
            out = kitchen.decide(dict(READY), TALK)
        self.assertEqual(out['status'], 'callback')
        self.assertIn('регион «Казань» не в списке', out['comment'])

    def test_unknown_status_and_empty_talk(self):
        self.assertEqual(kitchen.decide({'status': 'maybe'}, TALK)['status'], 'callback')
        self.assertEqual(kitchen.decide(dict(READY), '')['status'], 'ndz')


class SheetTests(unittest.TestCase):
    ATTEMPTS = [attempt(1, 'ndz', comment='Не дозвонился'),
                attempt(2, 'callback', '+7 (999) 000-11-22', 'Просит вечером', 60, {'callback_time': 'вечером'}),
                attempt(3, 'interested', '79990001122', 'Казань, плёнка', 1500, READY['facts']),
                attempt(4, 'not_relevant', '79995556677', 'Шаг 1: через полгода', 30)]
    BASE = {'9990001122': {'id': '1801', 'project': 'B3_кухонная гарнитура', 'base': '30.09.2026'}}

    def test_lead_row_matches_base_sheet(self):
        table = kitchen.calls_table(self.ATTEMPTS, self.BASE, MSK)
        self.assertEqual(table.rows[0], kitchen.HEADER)
        self.assertEqual(table.rows[1], ['База от 30.09.2026 · лидов 1'])
        row = dict(zip(kitchen.HEADER, table.rows[2]))
        self.assertEqual((row['Телефон'], row['id'], row['Дата базы']), ('+79990001122', '1801', '30.09.2026'))
        self.assertEqual((row['Звонок 1'], row['Звонок 2'], row['Звонок 3']), ('НДЗ', 'Перезвон', 'Заинтересованный'))
        self.assertEqual(row['Сумма, ₽'], 10 + 5 + 150)
        self.assertEqual(row['Дата звонка 2'], '07.10.2026 13:00')
        self.assertEqual(row['Имя'], 'Ирина Петровна')
        self.assertEqual(row['Напоминание'], '')  # последний итог не «Перезвон»
        self.assertTrue(row['Комментарий'].startswith('07.10 Не дозвонился\n07.10 Просит вечером'))
        self.assertEqual(table.rows[3], ['Нет в листе «Кухонные гарнитуры» · лидов 1'])
        self.assertEqual(table.rows[4][6:10], ['Неактуально', '', '', 10])

    def test_fourth_attempt_not_paid(self):
        many = [attempt(i, 'ndz', minutes=i) for i in range(1, 5)]
        row = kitchen.calls_table(many, {}, MSK).rows[2]
        self.assertEqual(row[6:10], ['НДЗ', 'НДЗ 2', 'НДЗ 3', 20])
        self.assertIn('Всего попыток: 4', row[5])
        days = kitchen.days_table(many, MSK)
        self.assertEqual(days.rows[1], ['07.10.2026', 1, 2, 3, 0, 20])

    def test_summary_periods(self):
        table = kitchen.summary_table(self.ATTEMPTS, MSK, datetime(2026, 10, 8, 9, tzinfo=UTC))
        rows = {r[0]: r for r in table.rows if r}
        self.assertEqual(rows['Всего'], ['Всего', 2, 2, 1, 175])
        self.assertEqual(rows['Сегодня'], ['Сегодня', 0, 1, 1, 150])  # третья попытка 08.10
        self.assertEqual(rows['Заинтересованный'], ['Заинтересованный', 1])
        self.assertEqual(rows['Лидов (номеров)'], ['Лидов (номеров)', 2])

    def test_foreign_sheet_requests_rejected(self):
        with self.assertRaises(RuntimeError):
            kitchen.own_only([{'repeatCell': {'range': {'sheetId': 2}}}], [7, 8])
        with self.assertRaises(RuntimeError):
            kitchen.own_title('Сводка')
        self.assertEqual(kitchen.own_only([{'repeatCell': {'range': {'sheetId': 7}}}], 7)[0]['repeatCell']['range']['sheetId'], 7)


class WriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_own_tabs_and_never_touches_human_ones(self):
        seen, next_id = [], iter(range(100, 110))

        def handler(request):
            body = json.loads(request.content) if request.content else None
            seen.append((request.method, request.url.path, body))
            if request.method == 'GET' and request.url.path.endswith('/values/\'Кухонные гарнитуры\'!A1:D'):
                return httpx.Response(200, json={'values': GRID})
            if request.method == 'GET':
                return httpx.Response(200, json=SHEETS)
            if body and 'requests' in body and 'addSheet' in body['requests'][0]:
                return httpx.Response(200, json={'replies': [{'addSheet': {'properties': {'sheetId': next(next_id)}}}]})
            return httpx.Response(200, json={})

        with patch.dict(os.environ, {'KITCHEN_SHEET_ID': 's1'}):
            leads = await kitchen.base(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token='t')
            self.assertEqual(leads['9990003344'], {'id': '1802', 'project': 'B3_кухонная гарнитура', 'base': '02.10.2026'})
            tables = [kitchen.calls_table(SheetTests.ATTEMPTS, leads, MSK),
                      kitchen.summary_table(SheetTests.ATTEMPTS, MSK, datetime(2026, 10, 7, 12, tzinfo=UTC)),
                      kitchen.days_table(SheetTests.ATTEMPTS, MSK)]
            await kitchen.write(tables, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token='t')

        added = [b['requests'][0]['addSheet']['properties'] for m, _, b in seen if b and 'requests' in b and 'addSheet' in b['requests'][0]]
        self.assertEqual([p['title'] for p in added], ['Ольга · Звонки', 'Ольга · Сводка', 'Ольга · По дням'])
        self.assertTrue(all('index' not in p for p in added))
        human = ('Сводка', 'По дням', 'Кухонные гарнитуры')
        for method, path, body in seen:
            if method != 'GET' and '/values/' in path:
                self.assertTrue(any(f"'{t}'" in path for t in ('Ольга · Звонки', 'Ольга · Сводка', 'Ольга · По дням')), path)
                self.assertFalse(any(path.split('/values/')[1].startswith(f"'{t}'") for t in human), path)
        ids = set()
        for _, _, body in seen:
            if body and 'requests' in body:
                ids |= set(_ids(body))
        self.assertTrue(ids <= {100, 101, 102}, ids)


def _ids(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from ([item] if key == 'sheetId' else _ids(item))
    elif isinstance(value, list):
        for item in value:
            yield from _ids(item)


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class KitchenStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        self.assertTrue((await self.pool.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        root = Path(__file__).resolve().parents[1]
        if not await self.pool.fetchval("SELECT to_regclass('clients') IS NOT NULL"):
            await self.pool.execute((root / 'tests/bootstrap.sql').read_text())
        await self.pool.execute("""
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS client_number TEXT;
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS raw_summary JSONB;
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS recording_id TEXT;""")
        await self.pool.execute((root / 'migrations/010_lead_attempts.sql').read_text())
        await self.pool.execute('TRUNCATE clients CASCADE')
        await self.pool.execute("INSERT INTO clients VALUES(1,'Europe/Moscow',true)")
        self.env = patch.dict(os.environ, {'KITCHEN_EXTENSIONS': '150', 'KITCHEN_SHEET_ID': 's1',
                                           'GOOGLE_SERVICE_ACCOUNT_FILE': '/x', 'KITCHEN_SINCE': '2026-10-07T00:00:00+00:00'})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await self.pool.close()

    async def call(self, number, talk, *, status='skipped', direction=2, ext='150', minutes=0):
        return await self.pool.fetchrow(
            """INSERT INTO calls(client_id,extension,status,client_number,call_started_at,raw_summary)
               VALUES(1,$1,$2,$3,$4,$5::jsonb) RETURNING *""", ext, status, number,
            datetime(2026, 10, 7, 9, tzinfo=timezone.utc) + timedelta(minutes=minutes),
            json.dumps({'context_type': direction, 'talk_duration': talk}))

    async def test_missed_calls_become_ndz_once(self):
        await self.call('79990001122', 0)
        await self.call('79990001122', 0, direction=1)  # входящий
        await self.call('79990001122', 0, ext='101')    # другой менеджер
        await self.call('79990001122', 12, status='new')  # разговор идёт в разбор
        self.assertEqual(await kitchen.record_missed(self.pool), 1)
        self.assertEqual(await kitchen.record_missed(self.pool), 0)
        self.assertEqual(await self.pool.fetchval('SELECT status FROM lead_attempts'), 'ndz')

    async def test_sync_writes_three_tabs_and_marks_written(self):
        missed = await self.call('79990001122', 0)
        talked = await self.call('79990001122', 40, status='analyzed', minutes=5)
        async with self.pool.acquire() as conn:
            await kitchen.record(conn, talked, kitchen.decide(dict(READY), TALK), 3.0)
        await kitchen.record_missed(self.pool)
        puts = {}

        def handler(request):
            if request.method == 'GET' and '/values/' in request.url.path:
                return httpx.Response(200, json={'values': GRID})
            if request.method == 'GET':
                return httpx.Response(200, json=SHEETS)
            body = json.loads(request.content) if request.content else {}
            if request.method == 'PUT':
                puts[request.url.path.split('/values/')[1]] = body['values']
            if 'addSheet' in str(body):
                return httpx.Response(200, json={'replies': [{'addSheet': {'properties': {'sheetId': 50 + len(puts)}}}]})
            return httpx.Response(200, json={})

        factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.assertEqual(await kitchen.sync(self.pool, http_factory=factory, access_token='t'), 2)
        row = puts["'Ольга · Звонки'!A1"][2]
        self.assertEqual(row[:4], ['+79990001122', '1801', 'B3_кухонная гарнитура', '30.09.2026'])
        self.assertEqual(row[6:10], ['НДЗ', 'Заинтересованный', '', 160])
        self.assertEqual(set(puts), {"'Ольга · Звонки'!A1", "'Ольга · Сводка'!A1", "'Ольга · По дням'!A1"})
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM lead_attempts WHERE written_at IS NULL'), 0)
        self.assertIsNotNone(missed)


    async def test_backfill_today_without_touching_calls(self):
        from scripts.kitchen_backfill import backfill
        await self.call('79990001122', 0)
        talked = await self.call('79990003344', 40, status='skipped', minutes=5)
        await self.pool.execute("UPDATE calls SET recording_id='r1' WHERE id=$1", talked['id'])
        await self.call('79990005566', 30, direction=1)  # входящий не берём
        heard = []

        async def transcribe(pool, call):
            heard.append(call['id'])
            return TALK

        async def run(transcript):
            return kitchen.decide(dict(READY), transcript), 2.0

        since = datetime(2026, 10, 7, tzinfo=UTC)
        self.assertEqual(await backfill(self.pool, since, transcribe, run, dry_run=True), {'ndz': 1, 'talks': 1, 'failed': 0})
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM lead_attempts'), 0)
        self.assertEqual(await backfill(self.pool, since, transcribe, run), {'ndz': 1, 'talks': 1, 'failed': 0})
        self.assertEqual(heard, [talked['id']])
        self.assertEqual(await self.pool.fetchval('SELECT status FROM lead_attempts WHERE call_id=$1', talked['id']), 'interested')
        self.assertEqual(await self.pool.fetchval('SELECT status FROM calls WHERE id=$1', talked['id']), 'skipped')
        self.assertEqual(await backfill(self.pool, since, transcribe, run), {'ndz': 0, 'talks': 0, 'failed': 0})


class HandlerRouteTests(unittest.TestCase):
    def test_operator_extension_is_routed(self):
        with patch.dict(os.environ, {'KITCHEN_EXTENSIONS': '150, 151'}):
            self.assertTrue(kitchen.handles('151'))
            self.assertFalse(kitchen.handles('101'))
            self.assertFalse(kitchen.handles(None))
        self.assertIsNone(kitchen.level({'status': 'interested'}))


if __name__ == '__main__':
    unittest.main()
