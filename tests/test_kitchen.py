"""Прозвон лидов по скрипту кухонь: итог попытки и запись в таблицу. Данные вымышленные."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    ['id', 'Проект', 'Тег проекта', 'Телефон', 'Создано', 'Напоминание', 'Заметка', 'Комментарий', 'Звонок 1', 'Звонок 2', 'Звонок 3'],
    ['База от 30.09.2026'],
    [1, 'B3_кухонная гарнитура', '', 79990001122, '', '', '', 'просила после обеда'],
    [2, 'B3_кухонная гарнитура', '', 79990003344.0, '', '', '', '', 'НДЗ', 'НДЗ 2', 'Неактуально'],
]


def attempt(call_id, status, number='79990001122', comment='коммент'):
    return {'call_id': call_id, 'status': status, 'client_number': number, 'comment': comment}


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


class PlanTests(unittest.TestCase):
    def test_attempts_fill_next_column_and_keep_operator_comment(self):
        updates, results = kitchen.plan(GRID, [attempt(1, 'ndz', comment='Автоответчик'),
                                               attempt(2, 'callback', '+7 (999) 000-11-22', 'Просит вечером'),
                                               attempt(3, 'ndz')], '07.10')
        self.assertEqual(dict(updates)['I3'], 'НДЗ')
        self.assertEqual(dict(updates)['J3'], 'Перезвон')
        self.assertEqual(dict(updates)['K3'], 'НДЗ 3')
        self.assertEqual(dict(updates)['H3'], 'просила после обеда\n07.10 Автоответчик\n07.10 Просит вечером\n07.10 коммент')
        self.assertEqual(results[2], ('J3', None))

    def test_float_phone_full_row_and_unknown_number(self):
        updates, results = kitchen.plan(GRID, [attempt(4, 'interested', '79990003344', 'Казань'),
                                               attempt(5, 'ndz', '79995556677')], '07.10')
        self.assertEqual(updates, [('H4', '07.10 Казань (сверх трёх попыток)')])
        self.assertEqual(results[4], ('H4', None))
        self.assertEqual(results[5], (None, 'номер не найден в таблице'))


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
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS raw_summary JSONB;""")
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

    async def test_flush_writes_cells_in_call_order(self):
        missed = await self.call('79990001122', 0)
        talked = await self.call('79990001122', 40, status='analyzed', minutes=5)
        async with self.pool.acquire() as conn:
            await kitchen.record(conn, talked, kitchen.decide(dict(READY), TALK), 3.0)
        await kitchen.record_missed(self.pool)
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
            if request.method == 'GET':
                return httpx.Response(200, json={'values': GRID})
            return httpx.Response(200, json={})

        written = await kitchen.flush(self.pool, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                                      access_token='tok')
        self.assertEqual(written, 2)
        data = {d['range']: d['values'][0][0] for d in seen[1][2]['data']}
        self.assertEqual(data["'Кухонные гарнитуры'!I3"], 'НДЗ')
        self.assertEqual(data["'Кухонные гарнитуры'!J3"], 'Заинтересованный')
        self.assertEqual(seen[1][2]['valueInputOption'], 'RAW')
        cells = dict(await self.pool.fetch('SELECT call_id, sheet_cell FROM lead_attempts'))
        self.assertEqual(cells, {missed['id']: 'I3', talked['id']: 'J3'})
        self.assertEqual(await kitchen.flush(self.pool, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                                             access_token='tok'), 0)


class HandlerRouteTests(unittest.TestCase):
    def test_operator_extension_is_routed(self):
        with patch.dict(os.environ, {'KITCHEN_EXTENSIONS': '150, 151'}):
            self.assertTrue(kitchen.handles('151'))
            self.assertFalse(kitchen.handles('101'))
            self.assertFalse(kitchen.handles(None))
        self.assertIsNone(kitchen.level({'status': 'interested'}))


if __name__ == '__main__':
    unittest.main()
