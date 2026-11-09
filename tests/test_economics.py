from __future__ import annotations

import json
import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import asyncpg

from crm import economics

DSN = os.getenv('METHODOLOGY_TEST_DATABASE_URL')


class ProviderEconomicsTests(unittest.IsolatedAsyncioTestCase):
    async def test_mango_uses_company_credentials_and_returns_only_balance(self):
        pool = AsyncMock()
        pool.fetchrow.return_value = {'vpbx_api_key_enc': 'encrypted-key', 'vpbx_api_salt_enc': 'encrypted-salt'}
        response = {'result': 1000, 'balance': 0, 'currency': 'RUB', 'sign': 'synthetic-response-secret'}
        with (
            patch.object(economics, 'decrypt', side_effect=['synthetic-key', 'synthetic-salt']),
            patch.object(economics.mango_client, 'call', new_callable=AsyncMock, return_value=response) as call,
        ):
            result = await economics.mango(pool, 7)
        self.assertEqual(pool.fetchrow.await_args.args[-1], 7)
        call.assert_awaited_once_with('account/balance', 'synthetic-key', 'synthetic-salt', {},
                                      timeout=economics.PROVIDER_TIMEOUT)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['metrics'], [{'name': 'Баланс ВАТС', 'amount': 0.0, 'unit': 'RUB'}])
        self.assertNotIn('synthetic', json.dumps(result))

    async def test_provider_failure_and_bad_numeric_response_are_unavailable(self):
        pool = AsyncMock()
        pool.fetchrow.return_value = {'vpbx_api_key_enc': 'encrypted-key', 'vpbx_api_salt_enc': 'encrypted-salt'}
        for value in (RuntimeError('response contains synthetic-secret'), {'result': 1000, 'balance': 'NaN', 'currency': 'RUB'}):
            kwargs = {'side_effect': value} if isinstance(value, Exception) else {'return_value': value}
            with (
                patch.object(economics, 'decrypt', return_value='synthetic-key'),
                patch.object(economics.mango_client, 'call', new_callable=AsyncMock, **kwargs),
            ):
                result = await economics.mango(pool, 3)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['metrics'], [])
            self.assertNotIn('synthetic', json.dumps(result))

    async def test_openrouter_allowlists_only_numbers_and_treats_null_limit_as_unknown(self):
        response = {'data': {'usage_monthly': 7.45, 'limit': None, 'limit_remaining': None, 'label': 'synthetic-key'}}
        with (
            patch.dict(os.environ, {'OPENROUTER_API_KEY': 'synthetic-key'}),
            patch.object(economics, 'get_json', new_callable=AsyncMock, return_value=response),
        ):
            result = await economics.openrouter()
        self.assertEqual(result['metrics'], [{'name': 'Расход за месяц', 'amount': 7.45, 'unit': 'USD'}])
        self.assertNotIn('synthetic', json.dumps(result))

    async def test_other_company_only_gets_own_mango_metadata(self):
        pool = AsyncMock()
        own_mango = {'name': 'Mango Office', 'scope': 'company'}
        with (
            patch.object(economics.store, 'zone', new_callable=AsyncMock, return_value=ZoneInfo('Europe/Moscow')),
            patch.object(economics, 'expense_rows', new_callable=AsyncMock, return_value=[]),
            patch.object(economics.company, 'ai_limit_rub', new_callable=AsyncMock, return_value=10),
            patch.object(economics.company, 'spent_today_rub', new_callable=AsyncMock, return_value=2),
            patch.object(economics, 'home_client_id', new_callable=AsyncMock, return_value=1),
            patch.object(economics, 'mango', new_callable=AsyncMock, return_value=own_mango) as mango,
            patch.object(economics, 'get_json', new_callable=AsyncMock) as external,
        ):
            result = await economics.overview(pool, 2, 7)
        mango.assert_awaited_once_with(pool, 2)
        external.assert_not_called()
        self.assertEqual(result['providers'], [own_mango])
        self.assertEqual((result['today_rub'], result['ai_limit_rub']), (2, 10))
        self.assertEqual((date.fromisoformat(result['to']) - date.fromisoformat(result['from'])).days, 6)
        with self.assertRaises(ValueError):
            await economics.overview(pool, 2, 365)


@unittest.skipUnless(DSN, 'Нужна локальная тестовая PostgreSQL')
class ExpenseEconomicsTests(unittest.IsolatedAsyncioTestCase):
    async def test_expenses_use_tenant_and_local_date_window_without_mixing_currencies(self):
        target = urlsplit(DSN)
        self.assertIn(target.hostname, {'127.0.0.1', 'localhost', '::1'})
        self.assertTrue(target.path.startswith('/callbot_methodology_test_'))
        connection = await asyncpg.connect(DSN)
        try:
            await connection.execute("""
                CREATE TEMP TABLE astra_daily_spend (client_id integer, day date, spent_units numeric);
                CREATE TEMP TABLE crm_questions (client_id integer, created_at timestamptz, cost_rub numeric);
                CREATE TEMP TABLE rop_agent_daily_spend (client_id integer, day date, spent_usd numeric);
                CREATE TEMP TABLE robot_options (key text);
                CREATE TEMP TABLE robot_calls (owner text, at timestamptz, tts_chars int, llm_tokens int, duration_s int);
                INSERT INTO astra_daily_spend VALUES
                    (1, '2026-11-08', 200000), (1, '2026-11-07', 1000000), (2, '2026-11-08', 1000000);
                INSERT INTO crm_questions VALUES
                    (1, '2026-11-07T21:00:00Z', 1.50), (1, '2026-11-08T20:59:59Z', 2.75),
                    (1, '2026-11-08T21:00:00Z', 100), (2, '2026-11-08T20:59:59Z', 200);
                INSERT INTO rop_agent_daily_spend VALUES (1, '2026-11-08', 3), (2, '2026-11-08', 30);
                INSERT INTO robot_calls VALUES
                    ('', '2026-11-07T21:00:00Z', 1000, 1000, 16),
                    ('Клиент Averon', '2026-11-08T20:59:59Z', 2000, 2000, 15),
                    ('#2', '2026-11-08T20:59:59Z', 1000000, 1000000, 500),
                    ('', '2026-11-08T21:00:00Z', 1000000, 1000000, 500);
            """)
            day = date(2026, 11, 8)
            start, end = datetime(2026, 11, 7, 21, tzinfo=timezone.utc), datetime(2026, 11, 8, 21, tzinfo=timezone.utc)
            with patch.dict(os.environ, {'AI_RUB_PER_MILLION_UNITS': '50'}):
                rows = await economics.expense_rows(connection, 1, day, day, start, end)
            amounts = {item['name']: (item['amount'], item['unit']) for item in rows}
            self.assertAlmostEqual(amounts['Разборы звонков и проверка промтов'][0], 10)
            self.assertEqual(amounts['Разборы звонков и проверка промтов'][1], 'RUB')
            self.assertEqual(amounts['AveronGPT'], (4.25, 'RUB'))
            self.assertEqual(amounts['Агент РОПа'], (3.0, 'USD'))
            prices = {'tts_1k_chars': 2, 'llm_1k_tokens': 3}
            with patch.object(economics.robot_store, 'prices', new_callable=AsyncMock, return_value=prices):
                robot = await economics.robot_expense(connection, start, end)
            self.assertAlmostEqual(robot['amount'], 15 + 3 * economics.robot_store.ASR_15S)
        finally:
            await connection.close()
