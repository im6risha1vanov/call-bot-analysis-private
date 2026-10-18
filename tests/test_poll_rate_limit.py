"""Лимит запросов Mango (429): тихий повтор, сообщение владельцу только при долгом простое."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')

import astra_worker as worker

LIMIT = RuntimeError('Mango stats/calls/request 429: {"name":"Too Many Requests"}')


class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        worker._rate_limited_since.clear()
        self.pool = AsyncMock()
        self.pool.fetch.return_value = [{'id': 1, 'mango_poll_interval_sec': 300}]

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
