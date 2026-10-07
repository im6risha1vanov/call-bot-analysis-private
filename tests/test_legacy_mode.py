"""Methodology switches and explicit legacy reviews; all dependencies are fake."""
import copy
import json
from contextlib import AbstractAsyncContextManager
import unittest
from unittest.mock import AsyncMock, patch

from automatic import jobs
from methodology import jobs as course_jobs, runtime
from tests.test_comparison import legacy_score
from tests.test_unified import sample
from tools import Actor
from unified import reanalysis, store


class Context(AbstractAsyncContextManager):
    def __init__(self, value): self.value = value
    async def __aenter__(self): return self.value
    async def __aexit__(self, *args): return False


class FakeDatabase:
    def __init__(self, row):
        self.row = copy.deepcopy(row)
        self.items = {}
        self.operations = []

    def acquire(self): return Context(self)
    def transaction(self): return Context(self)

    async def fetchrow(self, sql, *args):
        if 'FROM call_report_runs' in sql: return self.row
        if 'FROM clients' in sql:
            return {'id': 1, 'timezone': 'Europe/Moscow', 'processing_enabled': True}
        if 'FROM call_report_generation' in sql: return self.items[args[1]]
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        if 'pg_try_advisory_lock' in sql: return True
        if 'SELECT id FROM call_report_runs' in sql: return None
        if 'SELECT result FROM call_report_generation' in sql:
            stage = 'score' if "stage='score'" in sql else 'combined'
            return self.items[stage].get('result')
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        self.operations.append((sql, args))
        if 'SET configuration=' in sql:
            self.row['configuration'] = json.loads(args[1])
        elif 'INSERT INTO call_report_generation' in sql:
            self.items[args[1]] = {'status': 'pending', 'response_text': None,
                                  'prompt': args[2], 'settings': json.loads(args[3])}
        elif 'UPDATE call_report_generation' in sql:
            item = self.items[args[1]]
            if "status='processing'" in sql: item['status'] = 'processing'
            elif "status='received'" in sql:
                item.update(status='received', response_text=args[2])
            elif "status='complete'" in sql:
                item.update(status='complete', result=json.loads(args[2]))
        elif 'SET primary_analysis=' in sql:
            self.row.update(primary_analysis=json.loads(args[1]), auxiliary=json.loads(args[2]),
                            summary=args[3], status='ready')


class LegacyModeTests(unittest.IsolatedAsyncioTestCase):
    def row(self):
        row = sample()
        row.update(status='pending', client_id=1, transcript_sha256='synthetic')
        row['configuration'].update(criteria=reanalysis.analysis.CRITERIA)
        return row

    def actor(self): return Actor(123, 1, 'owner', 'owner')

    async def test_legacy_switch_prevents_both_automatic_enqueue_paths_without_db(self):
        config = {**runtime.settings(), 'mode': 'legacy'}
        pool = AsyncMock()
        with patch.object(runtime, 'settings', return_value=config):
            self.assertFalse(runtime.shadow_enabled())
            self.assertFalse(jobs.enabled())
            self.assertFalse(await jobs.enqueue(pool, 1, 123, 'Синтетический текст', {}))
            await course_jobs.enqueue(pool, 1, 'call', 123, 'Синтетический текст')
        self.assertEqual(pool.mock_calls, [])

    async def test_new_explicit_legacy_review_one_paid_stage_no_course_package(self):
        pool = FakeDatabase(self.row())
        actor = self.actor()
        with patch.object(reanalysis, 'get_call', AsyncMock(return_value=pool.row)), \
             patch.object(store, 'capture', AsyncMock(return_value=1)), \
             patch.object(jobs, 'enabled', return_value=False), \
             patch.object(reanalysis.evaluation, 'verify', side_effect=AssertionError('course package')), \
             patch.object(reanalysis.evaluation, 'prompt', side_effect=AssertionError('course prompt')):
            run_id = await reanalysis.create(pool, actor, 123, 'synthetic-preview')
        self.assertEqual(list(pool.items), ['score'])
        self.assertEqual(pool.row['configuration']['generation_stages'], ['score'])
        self.assertFalse(pool.row['configuration']['expect_combined'])
        paid = AsyncMock(return_value=(json.dumps(legacy_score()), 12, {}))
        with patch.object(reanalysis, 'resolve_actor', AsyncMock(return_value=actor)), \
             patch.object(store, 'access', AsyncMock(side_effect=lambda *args: pool.row)), \
             patch.object(reanalysis, 'over_budget', AsyncMock(return_value=False)), \
             patch.object(reanalysis.transport, 'request', paid), \
             patch.object(reanalysis.evaluation, 'verify', side_effect=AssertionError('course package')):
            self.assertEqual((await reanalysis.run(pool, actor, run_id))['status'], 'ready')
            self.assertTrue((await reanalysis.run(pool, actor, run_id))['cached'])
        paid.assert_awaited_once()
        self.assertNotIn('combined', pool.row['auxiliary'])
        self.assertEqual(pool.row['auxiliary']['sources'], {})
        self.assertIn('📊 Оценка звонка', pool.row['summary'])
        self.assertFalse(any('INSERT INTO automatic_call_spend' in sql for sql, _ in pool.operations))
        self.assertEqual(sum('INSERT INTO astra_daily_spend' in sql for sql, _ in pool.operations), 1)

    async def test_pending_old_request_cannot_pay_for_disabled_course(self):
        pool = FakeDatabase(self.row())
        actor = self.actor()
        pool.row['configuration'].update(explicit_generation=True, initiator=123,
                                        combined_package_sha256='test')
        pool.items = {'score': {'status': 'complete', 'result': {}},
                      'combined': {'status': 'pending', 'response_text': None}}
        with patch.object(reanalysis, 'resolve_actor', AsyncMock(return_value=actor)), \
             patch.object(store, 'access', AsyncMock(return_value=pool.row)), \
             patch.object(jobs, 'enabled', return_value=False), \
             patch.object(reanalysis.evaluation, 'verify', return_value={'sha256': 'test'}), \
             patch.object(reanalysis.transport, 'request', AsyncMock()) as paid:
            result = await reanalysis.run(pool, actor, 1)
        self.assertEqual(result['status'], 'methodology_disabled')
        paid.assert_not_awaited()
        self.assertEqual(pool.items['combined']['status'], 'pending')

    def test_old_request_stages_remain_pinned_and_invalid_stages_rejected(self):
        self.assertEqual(reanalysis.generation_stages({}), ['score', 'combined'])
        self.assertEqual(reanalysis.generation_stages({'generation_stages': ['score']}), ['score'])
        with self.assertRaises(ValueError):
            reanalysis.generation_stages({'generation_stages': ['course']})
