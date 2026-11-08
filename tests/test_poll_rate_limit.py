"""Лимит запросов Mango (429): тихий повтор, сообщение владельцу только при долгом простое."""
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')

import astra_worker as worker

LIMIT = RuntimeError('Mango stats/calls/request 429: {"name":"Too Many Requests"}')


class StatsWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_all_date_groups_and_pages(self):
        first_page = [{'entry_id': str(i)} for i in range(2000)]
        last_call = {'entry_id': 'next'}
        mango = AsyncMock(side_effect=[
            {'key': 'first'},
            {'status': 'complete', 'data': [{'list': first_page[:1999]}, {'list': first_page[1999:]}]},
            {'key': 'second'},
            {'status': 'complete', 'data': {'list': [last_call]}},
        ])
        start, end = datetime(2026, 11, 6), datetime(2026, 11, 8)
        with patch.object(worker.mango_client, 'call', mango):
            calls = await worker._fetch_calls_window('key', 'salt', start, end)

        self.assertEqual(calls, [*first_page, last_call])
        requests = [call.args[3] for call in mango.await_args_list if call.args[0] == 'stats/calls/request']
        self.assertEqual(requests, [
            {'start_date': '06.11.2026 00:00:00', 'end_date': '08.11.2026 00:00:00',
             'limit': '2000', 'offset': offset}
            for offset in ('0', '2000')
        ])

    async def test_failed_next_page_does_not_advance_the_sync_cursor(self):
        pool = AsyncMock()
        client = {'id': 1, 'timezone': 'Europe/Moscow', 'last_call_synced_at': None,
                  'vpbx_api_key_enc': 'key', 'vpbx_api_salt_enc': 'salt'}
        mango = AsyncMock(side_effect=[
            {'key': 'first'},
            {'status': 'complete', 'data': [{'list': [{'entry_id': str(i)} for i in range(2000)]}]},
            LIMIT,
        ])
        with patch.object(worker.mango_client, 'call', mango), patch.object(worker, 'decrypt', side_effect=str):
            with self.assertRaisesRegex(RuntimeError, '429'):
                await worker.poll_client_calls(pool, client)
        pool.execute.assert_not_awaited()
        pool.fetchval.assert_not_awaited()


class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        worker._rate_limited_since.clear()
        self.pool = AsyncMock()
        self.pool.fetch.return_value = [{'id': 1, 'mango_poll_interval_sec': 300, 'vpbx_api_key_enc': 'synthetic'}]

    async def run_at(self, moment, error=LIMIT, polls=None):
        with patch.object(worker.time, 'monotonic', return_value=moment), \
             patch.object(worker, 'poll_client_calls', AsyncMock(side_effect=error, return_value=(0, 0))), \
             patch.object(worker, '_send_poll_error', AsyncMock()) as notice:
            await worker.poll_all_clients(self.pool, polls if polls is not None else {})
        return notice

    async def test_short_limit_is_silent_and_retried_sooner(self):
        polls = {}
        self.assertFalse((await self.run_at(1000, polls=polls)).await_count)
        self.assertEqual(polls[1], 1000 + worker.RATE_LIMIT_RETRY_SEC)
        self.assertFalse((await self.run_at(1000 + 30 * 60)).await_count)

    async def test_long_limit_is_reported_and_success_resets(self):
        await self.run_at(1000)
        self.assertEqual((await self.run_at(1000 + 46 * 60)).await_count, 1)
        await self.run_at(5000, error=None)
        self.assertNotIn(1, worker._rate_limited_since)

    async def test_other_errors_reported_at_once(self):
        self.assertEqual((await self.run_at(1000, error=RuntimeError('Mango 500: boom'))).await_count, 1)


if __name__ == '__main__':
    unittest.main()
