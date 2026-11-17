import os
import unittest
from unittest.mock import patch

from crm import callcenter_export as export


class ExportTests(unittest.TestCase):
    def card(self, **kwargs):
        return {'project': 'packing', 'phone': '9991234567', 'category': 'qualified',
                'reason': 'Согласен', 'history': [{'at': '2026-11-17T12:00:00+03:00'}], **kwargs}

    def test_identity_is_stable_and_project_scoped(self):
        card = self.card()
        self.assertEqual(export.card_id(card), export.card_id(self.card(reason='Другой результат')))
        self.assertNotEqual(export.card_id(card), export.card_id(self.card(project='hh_ru')))
        self.assertNotEqual(export.card_id(card), export.card_id(self.card(phone='9991234568')))

    def test_card_rows_are_deterministic_and_preserve_history(self):
        card = self.card(history=[{'at': '2026-11-17', 'operator': 'olga'}, {'at': '2026-11-18', 'operator': 'oksana'}])
        with patch.dict(os.environ, {'CALLCENTER_CRM_URL': 'https://averonleads.ru'}):
            row = export.render(card)
            self.assertEqual(row, export.render(card))
        self.assertEqual(len(row), len(export.HEADERS))
        self.assertIn('Оксана', row[7])
        self.assertEqual(row[6], '18.11.2026 00:00')

    def test_destinations_are_explicit_and_distinct(self):
        with patch.dict(os.environ, {'CALLCENTER_EXPORT_SHEETS': '{"qualified":"a","reusable":"a","competitor":"b"}'}):
            with self.assertRaises(ValueError): export.destinations()
        with patch.dict(os.environ, {'CALLCENTER_EXPORT_SHEETS': '{"qualified":"a","reusable":"b","competitor":"c"}'}):
            self.assertEqual(export.destinations()['qualified'], 'a')

    def test_phone_must_be_normalized(self):
        with self.assertRaises(ValueError): export.card_id(self.card(phone='+7 999 123-45-67'))


class ReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_ids_rejected(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'values': [export.HEADERS, ['cc1:same'], ['cc1:same']]}
        class Http:
            async def get(self, *args, **kwargs): return Response()
        with self.assertRaises(ValueError): await export._read(Http(), 'sheet')

class GridTests(unittest.IsolatedAsyncioTestCase):
    async def test_grid_expands_before_overflow(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'sheets': [{'properties': {'title': 'Лиды', 'sheetId': 37, 'gridProperties': {'rowCount': 1000}}}]}
        class Http:
            def __init__(self): self.posts = []
            async def get(self, *args, **kwargs): return Response()
            async def post(self, url, **kwargs): self.posts.append(kwargs['json']); return Response()
        http = Http()
        await export._grow(http, 'sheet', 1001)
        self.assertEqual(http.posts[0]['requests'][0]['appendDimension']['sheetId'], 37)
        self.assertGreaterEqual(http.posts[0]['requests'][0]['appendDimension']['length'], 1)
        http.posts.clear()
        await export._grow(http, 'sheet', 1000)
        self.assertFalse(http.posts)

    async def test_wrong_headers_do_not_write(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'values': [['Other']]}
        class Http:
            async def get(self, *args, **kwargs): return Response()
        with self.assertRaises(ValueError): await export._read(Http(), 'sheet')

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_sheet_write_before_db_failure_does_not_duplicate(self):
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock
        from crm import callcenter_insights

        class Conn:
            def __init__(self): self.records = {}; self.fail = True; self.state = ''
            async def fetchval(self, sql, *args): return True
            async def fetch(self, sql, *args): return list(self.records.values())
            async def execute(self, sql, *args):
                if 'INSERT INTO callcenter_export_status' in sql: self.state = args[1]
                elif 'INSERT INTO callcenter_export_rows' in sql:
                    if self.fail: self.fail = False; raise RuntimeError('database temporarily unavailable')
                    _, key, category, sheet, payload = args
                    self.records[(key, category, sheet)] = dict(card_id=key, category=category, sheet_id=sheet, payload=payload)
                elif 'DELETE FROM callcenter_export_rows' in sql:
                    _, key, category, sheet = args
                    self.records.pop((key, category, sheet), None)
        conn = Conn()
        class Pool:
            async def execute(self, *args): pass
            @asynccontextmanager
            async def acquire(self): yield conn
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        pages = {k: [export.HEADERS] for k in ('a', 'b', 'c')}
        async def read(_http, sheet):
            rows = pages[sheet]
            return rows, {row[0]: n for n, row in enumerate(rows, 1) if n > 1 and row and row[0].startswith('cc1:')}
        async def put(_http, sheet, n, row):
            rows = pages[sheet]
            if n > len(rows): rows.append(row)
            else: rows[n - 1] = row
        card = {'project': 'packaging', 'phone': '9991234567', 'category': 'qualified', 'history': []}
        env = {'CALLCENTER_EXPORT_SHEETS': '{"qualified":"a","reusable":"b","competitor":"c"}',
               'CALLCENTER_CRM_URL': 'https://averonleads.ru', 'CALLCENTER_LEADS_FOLDER_ID': 'folder'}
        with patch.dict(os.environ, env), patch.object(callcenter_insights, 'cards', AsyncMock(return_value=[card])) as cards_mock, \
             patch.object(export.callcenter_base, 'snapshot', AsyncMock(return_value=('now', {'olga': {'tab': []}, 'oksana': {'tab': []}}))) as snapshot_mock, \
             patch.object(export, '_token', return_value='token'), patch.object(export.httpx, 'AsyncClient', Client), \
             patch.object(export, '_read', read), patch.object(export, '_put', put), patch.object(export, '_grow', AsyncMock()):
            await export.sync(Pool(), 1)
            self.assertEqual(conn.state, 'error')
            self.assertEqual(len(pages['a']), 2)
            await export.sync(Pool(), 1)
            self.assertEqual(conn.state, 'ready')
            self.assertEqual(len(pages['a']), 2)
            self.assertEqual(len(conn.records), 1)
            cards_mock.return_value = [{**card, 'stop_contact': True}]
            await export.sync(Pool(), 1)
            self.assertFalse(conn.records)
            self.assertFalse(any(pages['a'][1]))
            await export.sync(Pool(), 1)
            self.assertFalse(any(pages['a'][1]))

            cards_mock.return_value = [{**card, 'category': 'competitor', 'provenance': 'sheet_operator'}]
            await export.sync(Pool(), 1)
            self.assertTrue(pages['c'][1][4].startswith(export.SHEET_PROVENANCE))
            cards_mock.return_value = []
            original = list(pages['c'][1])
            pages['c'][1][4] = 'Изменено вручную'
            await export.sync(Pool(), 1)
            self.assertEqual(conn.state, 'error')
            self.assertEqual(pages['c'][1][4], 'Изменено вручную')
            pages['c'][1] = original
            snapshot_mock.return_value = ('now', {'olga': None, 'oksana': {'tab': []}})
            await export.sync(Pool(), 1)
            self.assertTrue(any(pages['c'][1]))
            snapshot_mock.return_value = ('now', {'olga': {'tab': []}, 'oksana': {'tab': []}})
            await export.sync(Pool(), 1)
            self.assertFalse(any(pages['c'][1]))
            self.assertFalse(conn.records)
