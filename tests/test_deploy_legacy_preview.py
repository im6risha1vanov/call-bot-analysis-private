"""Owner-only deployment helper checks; no systemd, DB, model or Telegram calls."""
from contextlib import AbstractAsyncContextManager
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from scripts import deploy_legacy_preview as deploy
from tools import Actor, Forbidden
from unified import store


class Context(AbstractAsyncContextManager):
    def __init__(self, value): self.value = value
    async def __aenter__(self): return self.value
    async def __aexit__(self, *args): return False


class Pool:
    def __init__(self, old_run=None, latest=123):
        self.old_run, self.latest = old_run, latest
        self.operations = []
    def acquire(self): return Context(self)
    def transaction(self): return Context(self)
    async def execute(self, sql, *args): self.operations.append((sql, args))
    async def fetchval(self, sql, *args):
        self.operations.append((sql, args))
        return self.latest if 'FROM calls' in sql else self.old_run


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    def owner(self): return Actor(1234, 7, 'owner', None)
    def row(self): return {'id': 50, 'call_id': 123, 'configuration': {
        'expect_course': False, 'expect_combined': False}}

    def test_six_minute_query_boundary_latest_and_client_isolation(self):
        query, params, key = deploy.selection(360)
        with sqlite3.connect(':memory:') as db:
            db.executescript('''CREATE TABLE calls(id,client_id,status,duration_seconds,call_started_at,transcript);
              CREATE TABLE astra_analysis(call_id,status,level,score);''')
            for call_id, client_id, duration, stamp in (
                (1, 7, 360, '2026-10-06T09:00:00'),
                (2, 7, 359, '2026-10-06T10:00:00'),
                (3, 8, 700, '2026-10-06T11:00:00'),
                (4, 7, 900, '2026-10-05T12:00:00')):
                db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?)',
                           (call_id, client_id, 'analyzed', duration, stamp, 'Вымышленная реплика. '*10))
                db.execute('INSERT INTO astra_analysis VALUES(?,?,?,?)', (call_id, 'analyzed', '✅', 70))
            bindings = {'1': 7, '2': params[0]}
            self.assertEqual(db.execute(query, bindings).fetchone()[0], 1)
            db.execute("UPDATE astra_analysis SET level=NULL WHERE call_id=1")
            self.assertEqual(db.execute(query, bindings).fetchone()[0], 4)
        self.assertEqual(key, deploy.KEY+':min360')
        self.assertNotEqual(key, deploy.KEY)

    async def test_six_minute_preview_is_separate_from_previous_short_preview(self):
        pool = Pool()
        row = self.row(); row['metadata'] = {'duration_seconds': 360}
        with patch.object(store, 'capture', AsyncMock(return_value=50)) as capture, \
             patch.object(store, 'access', AsyncMock(return_value=row)), \
             patch.object(store, 'prepare', AsyncMock(return_value=row)):
            await deploy.prepare_preview(pool, self.owner(), min_duration=360)
        capture.assert_awaited_once_with(pool, 123, 7, deploy.KEY+':min360', fresh=False)
        query, params, _ = deploy.selection(360)
        self.assertIn((query, (7, *params)), pool.operations)

    async def test_existing_preview_below_six_minutes_cannot_be_sent_as_long_call(self):
        row = self.row(); row['metadata'] = {'duration_seconds': 359}
        with patch.object(store, 'access', AsyncMock(return_value=row)), \
             patch.object(store, 'prepare', AsyncMock()) as prepare, self.assertRaises(ValueError):
            await deploy.prepare_preview(Pool(old_run=50), self.owner(), min_duration=360)
        prepare.assert_not_awaited()

    def test_minimum_duration_validation(self):
        for value in (-1, '360', True, 86401):
            with self.assertRaises(ValueError): deploy.selection(value)

    async def test_new_preview_uses_client_latest_saved_primary_no_fanout(self):
        pool = Pool()
        with patch.object(store, 'capture', AsyncMock(return_value=50)) as capture, \
             patch.object(store, 'access', AsyncMock(return_value=self.row())), \
             patch.object(store, 'prepare', AsyncMock(return_value=self.row())):
            result = await deploy.prepare_preview(pool, self.owner())
        self.assertEqual(result['id'], 50)
        capture.assert_awaited_once_with(pool, 123, 7, deploy.KEY, fresh=False)
        query = next(args for sql, args in pool.operations if sql == deploy.LATEST)
        self.assertEqual(query, (7,))
        self.assertNotIn('deliver', capture.await_args.kwargs)

    async def test_repeat_pins_original_preview_even_when_new_calls_arrive(self):
        pool = Pool(old_run=50, latest=456)
        with patch.object(store, 'capture', AsyncMock()) as capture, \
             patch.object(store, 'access', AsyncMock(return_value=self.row())), \
             patch.object(store, 'prepare', AsyncMock(return_value=self.row())):
            await deploy.prepare_preview(pool, self.owner())
        capture.assert_not_awaited()
        self.assertFalse(any(sql == deploy.LATEST for sql, args in pool.operations))

    async def test_manager_cannot_use_preview_helper(self):
        pool = Pool()
        with self.assertRaises(Forbidden):
            await deploy.prepare_preview(pool, Actor(12, 7, 'manager', '101'))
        self.assertEqual(pool.operations, [])

    async def test_no_saved_call_does_not_capture(self):
        with patch.object(store, 'capture', AsyncMock()) as capture, self.assertRaises(ValueError):
            await deploy.prepare_preview(Pool(latest=None), self.owner())
        capture.assert_not_awaited()

    async def test_stale_course_preview_not_silently_relabelled_legacy(self):
        row = self.row(); row['configuration']['expect_course'] = True
        with patch.object(store, 'access', AsyncMock(return_value=row)), \
             patch.object(store, 'prepare', AsyncMock()) as prepare, self.assertRaises(ValueError):
            await deploy.prepare_preview(Pool(old_run=50), self.owner())
        prepare.assert_not_awaited()

    async def test_owner_binding_missing_ambiguous_or_revoked_is_rejected(self):
        pool = AsyncMock()
        for rows in ([], [{'telegram_user_id': 1234}, {'telegram_user_id': 5678}]):
            pool.fetch.return_value = rows
            with self.assertRaises(Forbidden): await deploy.recipient(pool)
        pool.fetch.return_value = [{'telegram_user_id': 1234}]
        with patch('tools.resolve_actor', AsyncMock(return_value=Actor(1234, 7, 'manager', '101'))), \
             self.assertRaises(Forbidden):
            await deploy.recipient(pool)

    def test_restart_targets_only_main_and_checks_other_states(self):
        def output(cmd, **kwargs):
            if cmd[0] == 'journalctl': return ''
            return str(deploy.PROJECT) if 'WorkingDirectory' in cmd else str(deploy.PROJECT/'demo_bot.py')
        with patch.object(deploy.subprocess, 'check_output', side_effect=output), \
             patch.object(deploy.subprocess, 'run') as run, \
             patch.object(deploy, 'active', return_value='active'), \
             patch.object(deploy.time, 'sleep'), patch('builtins.print'):
            deploy.restart_main()
        run.assert_called_once_with(['systemctl', 'restart', 'callbot-astra.service'], check=True)

    def test_startup_errors_block_confirmation_without_exposing_log_text(self):
        def output(cmd, **kwargs):
            if cmd[0] == 'journalctl':
                return '{"MESSAGE":"Traceback (most recent call last) private log text"}\n'
            return str(deploy.PROJECT) if 'WorkingDirectory' in cmd else str(deploy.PROJECT/'demo_bot.py')
        with patch.object(deploy.subprocess, 'check_output', side_effect=output), \
             patch.object(deploy.subprocess, 'run') as run, \
             patch.object(deploy, 'active', return_value='active'), \
             patch.object(deploy.time, 'sleep'), patch('builtins.print') as print_mock, \
             self.assertRaises(RuntimeError):
            deploy.restart_main()
        self.assertNotIn('private log text', str(print_mock.call_args_list))
        run.assert_called_once_with(['systemctl', 'restart', 'callbot-astra.service'], check=True)
