import unittest
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager

from crm import callcenter_monitor as monitor, call_analysis


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def run_scan(self, full_at):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        class Conn:
            def __init__(self): self.day_args = None; self.day_sql = None; self.updates = []
            @asynccontextmanager
            async def transaction(self): yield self
            async def fetchval(self, sql, *args):
                if 'clock_timestamp' in sql: return now
                return True
            async def fetchrow(self, sql, *args): return {'scanned_at': now - timedelta(minutes=1), 'full_scanned_at': full_at, 'scan_signature': json.dumps([monitor.RULESET, [('olga', '12', 'auto')]], ensure_ascii=True, sort_keys=True)}
            async def execute(self, sql, *args): self.updates.append((sql, args))
            async def fetch(self, sql, *args): self.day_sql, self.day_args = sql, args; return []
        conn = Conn()
        class Pool:
            @asynccontextmanager
            async def acquire(self): yield conn
        employees = [{'extension': '12', 'archived_at': None, 'full_name': 'Ольга Петрова'}]
        specs = [{'id': 'olga', 'configured': True, 'extension': '12'}]
        env = {'CALLCENTER_MONITOR_CLIENT_ID': '1', 'CALLCENTER_MONITOR_FROM': '2026-11-17T00:00:00+03:00'}
        with patch.dict('os.environ', env), patch.object(call_analysis, '_employees', AsyncMock(return_value=employees)), patch.object(call_analysis, 'operator_specs', return_value=specs):
            await monitor.enqueue(Pool())
        return conn, now

    async def test_incremental_scan_covers_readiness_and_retry(self):
        full_at = datetime(2026, 11, 18, 11, tzinfo=timezone.utc)
        conn, now = await self.run_scan(full_at)
        self.assertFalse(conn.day_args[4])
        self.assertEqual(conn.day_args[3], now - timedelta(minutes=46))
        self.assertIn('updated_at >= $4', conn.day_sql)
        self.assertIn('retry.attempts < 3', conn.day_sql)
        self.assertIn("i.status='pending'", conn.day_sql)

    async def test_initial_and_daily_full_scans(self):
        for full_at in (None, datetime(2026, 11, 17, 11, tzinfo=timezone.utc)):
            conn, _ = await self.run_scan(full_at)
            self.assertTrue(conn.day_args[4])
            self.assertTrue(any('full_scanned_at=CASE' in sql for sql, _ in conn.updates))
