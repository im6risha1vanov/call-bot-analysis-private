import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from scripts import release_manager as release


CURRENT = '1' * 40
TARGET = '2' * 40


class ReleasePlanTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(release, 'STATE', self.state))
        self.plan = self.state/'release-crm.json'
        self.legacy = self.state/'release.json'
        self.legacy.write_text(json.dumps({'baseline': release.BASELINE, 'release': '3' * 40}))
        self.command = self.enterContext(patch.object(release, 'command'))
        self.commit = self.enterContext(patch.object(release, 'commit', side_effect=lambda ref: ref))
        self.units = self.enterContext(patch.object(release, 'units_for', return_value=[release.CRM_UNIT]))
        self.manifest = self.enterContext(patch.object(release, 'manifest', return_value={'crm/web.py': 'digest'}))
        self.switch = self.enterContext(patch.object(release, 'switch'))
        self.enterContext(patch('builtins.print'))

    def plan_args(self, action, *, plan='crm'):
        return SimpleNamespace(action=action, plan=plan, baseline=CURRENT, release=TARGET,
                               schema_compatible=action == 'rollback')

    def test_pins_both_commits_and_uses_their_manifest_for_rollback(self):
        old_plan = self.legacy.read_bytes()
        pending = self.plan.with_suffix('.tmp')
        pending.write_text('interrupted plan write')
        self.command.side_effect = [CURRENT, '', CURRENT, TARGET]
        release.run_plan(self.plan_args('init'), self.plan)
        self.assertEqual(json.loads(self.plan.read_text()), {
            'baseline': CURRENT, 'release': TARGET, 'baseline_manifest': {'crm/web.py': 'digest'},
        })
        self.assertFalse(pending.exists())
        self.assertEqual(self.legacy.read_bytes(), old_plan)
        release.run_plan(self.plan_args('deploy'), self.plan)
        self.switch.assert_called_once_with(TARGET, rollback=False, schema_compatible=False, baseline_manifest=None)
        self.switch.reset_mock()
        release.run_plan(self.plan_args('rollback'), self.plan)
        self.switch.assert_called_once_with(CURRENT, rollback=True, schema_compatible=True,
                                            baseline_manifest={'crm/web.py': 'digest'})

    def test_rejects_a_plan_without_crm(self):
        self.command.side_effect = [CURRENT, '']
        self.units.side_effect = [[release.CRM_UNIT], []]
        with self.assertRaisesRegex(RuntimeError, 'Both commits .* must contain CRM'):
            release.run_plan(self.plan_args('init'), self.plan)
        self.assertFalse(self.plan.exists())

    def test_legacy_plan_rejects_unrelated_working_crm_before_switching(self):
        self.command.return_value = CURRENT
        with self.assertRaisesRegex(RuntimeError, 'outside the pinned release plan'):
            release.run_plan(self.plan_args('rollback', plan=None), self.legacy)
        self.switch.assert_not_called()

    def test_named_rollback_rejects_a_checkout_that_is_already_at_baseline(self):
        self.plan.write_text(json.dumps({'baseline': CURRENT, 'release': TARGET}))
        self.command.return_value = CURRENT
        with self.assertRaisesRegex(RuntimeError, 'not the starting commit'):
            release.run_plan(self.plan_args('rollback'), self.plan)
        self.switch.assert_not_called()


class ReleaseReadinessTests(unittest.TestCase):
    def test_active_crm_unit_requires_http_readiness(self):
        with patch.object(release, 'command', return_value='active'), \
                patch.object(release, 'crm_http_ready', side_effect=[False, True]):
            self.assertFalse(release.healthy([release.CRM_UNIT]))
            self.assertTrue(release.healthy([release.CRM_UNIT]))

    def test_legacy_units_do_not_probe_crm(self):
        with patch.object(release, 'command', return_value='active'), \
                patch.object(release, 'crm_http_ready') as probe:
            self.assertTrue(release.healthy(release.UNITS))
        probe.assert_not_called()


class ReleaseSwitchTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.busy_after_stop = False
        self.status_calls = 0
        self.stop_fails_once = False
        self.migration_fails = False
        self.unhealthy = False
        self.target_has_crm = True
        self.migrations_differ = False
        self.unconfirmed_at = 0
        self.unresolved_review_at = 0
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(release, 'command', self.command))
        stack.enter_context(patch.object(release, 'database', self.database))
        stack.enter_context(patch.object(release, 'verify_files', self.verify_files))
        stack.enter_context(patch.object(release, 'wait_healthy', self.wait_healthy))
        stack.enter_context(patch('builtins.print'))

    def command(self, args, *, umask=-1):
        if args[:2] == ['systemctl', 'show']:
            if args[2] == '--property=ActiveState':
                return 'inactive' if args[-1] == 'callbot-crm.service' else 'active'
            return 'loaded'
        if args[:2] == ['systemctl', 'stop']:
            self.events.append(('stop', tuple(args[2:])))
            if self.stop_fails_once:
                self.stop_fails_once = False
                raise RuntimeError('partial stop failure')
            return ''
        if args[:2] == ['systemctl', 'start']:
            self.events.append(('start', tuple(args[2:])))
            return ''
        if args[:2] == ['git', 'rev-parse']:
            return args[3].split('^')[0] if args[2] == '--verify' else CURRENT
        if args[:2] == ['git', 'status']:
            return ''
        if args[:2] == ['git', 'diff']:
            return 'migrations/036_crm_clear_recoverable_passwords.sql' if self.migrations_differ else ''
        if args[:2] == ['git', 'ls-tree']:
            return 'crm/web.py' if args[3] == CURRENT or self.target_has_crm else ''
        if args[:2] == ['git', 'checkout']:
            self.assertEqual(umask, 0o022)
            self.events.append(('checkout', args[-1]))
            return ''
        self.fail(f'Unexpected command: {args}')

    async def database(self, action, *, release=None):
        if action == 'migrate':
            self.events.append(('migrate', release))
            if self.migration_fails:
                raise RuntimeError('migration status uncertain')
            return None
        self.status_calls += 1
        self.events.append(('status', self.status_calls))
        return {'processing_tasks': int(self.busy_after_stop and self.status_calls == 2), 'active_course_training': 0,
                'unconfirmed_automation_events': int(self.unconfirmed_at == self.status_calls),
                'unresolved_review_deliveries': int(self.unresolved_review_at == self.status_calls)}

    def verify_files(self, target, *, baseline_manifest=None):
        self.events.append(('verify', target))
        return 10

    def wait_healthy(self, units=()):
        self.events.append(('health', tuple(units)))
        if self.unhealthy:
            raise RuntimeError('service unhealthy')

    def test_migration_follows_quiescence_and_verification_and_includes_crm(self):
        release.switch(TARGET)
        units = (*release.UNITS, 'callbot-crm.service')
        self.assertEqual(self.events, [
            ('status', 1), ('stop', units), ('status', 2), ('checkout', TARGET),
            ('verify', TARGET), ('migrate', TARGET), ('start', units), ('health', units),
        ])

    def test_work_claimed_during_shutdown_aborts_before_migration(self):
        self.busy_after_stop = True
        with self.assertRaisesRegex(RuntimeError, 'Work started during shutdown'):
            release.switch(TARGET)
        self.assertEqual([kind for kind, _ in self.events], ['status', 'stop', 'status', 'start', 'health'])

    def test_partial_stop_failure_restarts_original_services_without_switching(self):
        self.stop_fails_once = True
        with self.assertRaisesRegex(RuntimeError, 'partial stop failure'):
            release.switch(TARGET)
        self.assertEqual([kind for kind, _ in self.events], ['status', 'stop', 'start', 'health'])

    def test_unknown_migration_result_keeps_services_stopped(self):
        self.migration_fails = True
        with self.assertRaisesRegex(RuntimeError, 'Services stopped'):
            release.switch(TARGET)
        self.assertEqual([kind for kind, _ in self.events], [
            'status', 'stop', 'status', 'checkout', 'verify', 'migrate', 'stop',
        ])

    def test_post_migration_health_failure_keeps_services_stopped(self):
        self.unhealthy = True
        with self.assertRaisesRegex(RuntimeError, 'Services stopped'):
            release.switch(TARGET)
        self.assertEqual([kind for kind, _ in self.events], [
            'status', 'stop', 'status', 'checkout', 'verify', 'migrate', 'start', 'health', 'stop',
        ])

    def test_rollback_with_different_migrations_requires_explicit_confirmation(self):
        self.migrations_differ = True
        self.target_has_crm = False
        with self.assertRaisesRegex(RuntimeError, 'Verify the database is compatible'):
            release.switch(TARGET, rollback=True)
        self.assertEqual(self.events, [])

        release.switch(TARGET, rollback=True, schema_compatible=True)
        self.assertIn(('stop', (*release.UNITS, 'callbot-crm.service')), self.events)
        self.assertIn(('start', tuple(release.UNITS)), self.events)
        self.assertNotIn(('migrate', TARGET), self.events)

    def test_status_reports_an_inactive_crm(self):
        state = release.status()
        self.assertEqual(state['commit'], CURRENT)
        self.assertIn({'unit': 'callbot-crm.service', 'active': 'inactive'}, state['services'])

    def test_rollback_rechecks_unconfirmed_events_after_stopping_services(self):
        self.unconfirmed_at = 2
        self.migrations_differ = True
        with self.assertRaisesRegex(RuntimeError, 'Reconcile unconfirmed automation events'):
            release.switch(TARGET, rollback=True, schema_compatible=True)
        self.assertEqual([kind for kind, _ in self.events], ['status', 'stop', 'status', 'start', 'health'])

    def test_rollback_is_blocked_before_stop_if_an_event_is_already_unconfirmed(self):
        self.unconfirmed_at = 1
        with self.assertRaisesRegex(RuntimeError, 'Reconcile unconfirmed automation events'):
            release.switch(TARGET, rollback=True)
        self.assertEqual(self.events, [('status', 1)])

    def test_rollback_rechecks_pending_review_delivery_after_stopping_services(self):
        self.unresolved_review_at = 2
        with self.assertRaisesRegex(RuntimeError, 'Reconcile outstanding review deliveries'):
            release.switch(TARGET, rollback=True)
        self.assertEqual([kind for kind, _ in self.events], ['status', 'stop', 'status', 'start', 'health'])


@unittest.skipUnless(os.getenv('METHODOLOGY_TEST_DATABASE_URL'), 'local audit database required')
class ReleaseDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_distinguishes_unconfirmed_from_pending_and_processed_events(self):
        import asyncpg

        dsn = os.environ['METHODOLOGY_TEST_DATABASE_URL']
        conn = await asyncpg.connect(dsn)
        self.addAsyncCleanup(conn.close)
        self.assertTrue((await conn.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        await conn.execute('CREATE TEMP TABLE tasks (status text)')
        await conn.execute('CREATE TEMP TABLE automation_events (processed_at timestamptz)')
        await conn.execute('CREATE TEMP TABLE review_delivery (status text)')
        proxy = SimpleNamespace(fetchval=conn.fetchval, close=AsyncMock())
        with patch('asyncpg.connect', AsyncMock(return_value=proxy)), patch('dotenv.dotenv_values', return_value={'DATABASE_URL': dsn}):
            self.assertEqual((await release.database('status'))['unconfirmed_automation_events'], 0)
            await conn.execute('ALTER TABLE automation_events ADD COLUMN claimed_at timestamptz')
            await conn.execute('INSERT INTO automation_events VALUES (NULL, NULL), (NULL, now()), (now(), now())')
            self.assertEqual((await release.database('status'))['unconfirmed_automation_events'], 1)
            await conn.execute("INSERT INTO review_delivery VALUES ('pending'), ('sending'), ('uncertain'), ('sent'), ('failed')")
            self.assertEqual((await release.database('status'))['unresolved_review_deliveries'], 3)
