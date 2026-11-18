import asyncio
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

from aiohttp.test_utils import TestClient, TestServer

from crm import admin_integrations, admin_pipelines, admin_rules, auth, automations, web
from tests.test_crm import DBCase, DSN


BASE = '/api/platform/admin'


class AdminSpecTests(unittest.TestCase):
    def test_an_edit_can_reuse_only_a_webhook_url_from_the_current_rule(self):
        url = 'https://example.test/hook-with-a-private-token'
        current = {'actions': [{'type': 'email.send'}, {'type': 'webhook.send', 'url': url}]}
        update = {'actions': [{'type': 'webhook.send', 'source_action': 1}]}
        self.assertEqual(admin_rules.restore_action_urls(update, current)['actions'][0]['url'], url)
        with self.assertRaises(ValueError):
            admin_rules.restore_action_urls(update, {'actions': []})

    def test_openapi_matches_registered_operations(self):
        from crm import admin_api

        registered = set()
        for rule in admin_api.routes(None, None, None):
            match = re.search(r'\{action:([^}]+)\}', rule.path)
            choices = match[1].split('|') if match else [None]
            for action in choices:
                path = rule.path.replace(match[0], action) if match else rule.path
                path = re.sub(r'\{(\w+):\\d\{\d,\d+\}\}', r'{\1}', path)
                registered.add((rule.method.lower(), path))
        registered.add(('post', '/api/deals/{phone}/stage'))
        path = Path(__file__).resolve().parents[1] / 'docs/platform-admin.openapi.json'
        spec = json.loads(path.read_text(encoding='utf-8'))
        documented = {(method, route) for route, methods in spec['paths'].items() for method in methods}
        self.assertEqual(registered, documented)


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL is required')
class PlatformAdminTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        root = Path(__file__).resolve().parents[1]
        for name in ('030_company_settings.sql', '030_automations.sql', '037_automation_webhook_key.sql',
                     '038_automation_event_claim.sql', '055_crm_platform_admin.sql', '055_crm_platform_admin.sql'):
            await self.pool.execute((root / 'migrations' / name).read_text(encoding='utf-8'))
        await self.pool.execute('TRUNCATE crm_admin_requests, automation_events')
        await auth.account_create(self.pool, 1, 'director', 'grigory-test', password='test-grigory-1')
        self.admin = await self.pool.fetchval("SELECT id FROM crm_invites WHERE login='grigory-test'")
        self.enterContext(patch.dict(os.environ, {'CRM_HOME_COMPANY': '1',
                                                  'CRM_PLATFORM_ADMIN_INVITE_ID': str(self.admin),
                                                  'CRM_PLATFORM_ADMIN_WRITES': '1'}))
        self.http = TestClient(TestServer(web.build(self.pool)))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)
        self.calls = 0

    async def session(self, who, role='director', *, support=None, company=1, extension=None):
        self.calls += 1
        token = f'local-test-session-{self.calls:03}'
        await self.pool.execute(
            '''INSERT INTO crm_sessions(token_hash,expires_at,role,client_id,invite_id,support_by,extension)
               VALUES($1,now()+'1 hour',$2,$3,$4,$5,$6)''',
            auth.digest(token), role, company, who, support, extension)
        self.http.session.cookie_jar.update_cookies({'crm_session': token})

    async def request(self, method, path, payload=None, *, key=None, status=200):
        self.calls += 1
        response = await self.http.request(
            method, path, json=payload, headers={'X-CRM': '1', 'Idempotency-Key': key or f'local-test-{self.calls:03}'})
        self.assertEqual(response.status, status, await response.text())
        return await response.json()

    async def test_identity_versioning_restore_and_audit(self):
        await self.request('GET', BASE + '/catalog', status=401)
        await auth.account_create(self.pool, 1, 'director', 'other-admin', password='test-other-admin')
        other = await self.pool.fetchval("UPDATE crm_invites SET platform=true WHERE login='other-admin' RETURNING id")
        await self.session(other)
        await self.request('GET', BASE + '/catalog', status=403)
        await self.session(self.admin, support=other)
        await self.request('GET', BASE + '/catalog', status=403)
        await self.session(self.admin)
        path = BASE + '/companies/1'
        result = await self.request('GET', path)
        self.assertEqual(result['version'], 0)
        with patch.dict(os.environ, {'CRM_PLATFORM_ADMIN_WRITES': '0'}):
            await self.request('PATCH', path + '/settings', {'version': 0, 'name': 'Read only'}, status=403)
        await self.pool.execute(
            """INSERT INTO company_settings(client_id,settings) VALUES(1,'{"other_key":"kept"}')""")
        key = 'same-patch-001'
        body = {'version': 0, 'name': 'Averon 1', 'settings': {'ai_day_limit_rub': 0}}
        first, repeated = await asyncio.gather(*[
            self.request('PATCH', path + '/settings', body, key=key) for _ in range(2)
        ])
        self.assertEqual(first, repeated)
        self.assertEqual(first['version'], 1)
        self.assertTrue((await self.request('GET', path))['ai_limit_reached'])
        self.assertEqual(await self.pool.fetchval("SELECT settings->>'other_key' FROM company_settings WHERE client_id=1"),
                         'kept')
        await self.request('PATCH', path + '/settings', {'version': 1, 'name': 'Averon 2'}, key=key, status=409)
        await self.request('PATCH', path + '/settings', {'version': 0, 'name': 'Old tab'}, status=409)
        await self.request('PATCH', path + '/settings', {'version': 1, 'name': 'Averon 2'})
        journal = (await self.request('GET', path + '/audit'))['items']
        self.assertEqual(len(journal), 2)
        restored = await self.request('POST', path + f"/settings/restore/{journal[1]['id']}", {'version': 2})
        self.assertEqual(restored['name'], 'Averon 1')
        self.assertEqual(restored['version'], 3)
        restored = await self.request('POST', path + f"/settings/restore/{journal[1]['id']}",
                                      {'version': 3, 'state': 'before'})
        self.assertEqual(restored['name'], 'Averon')

    async def test_company_accounts_and_live_role_restrictions(self):
        await self.session(self.admin)
        created = await self.request('POST', BASE + '/companies',
                                     {'name': 'Second sales team', 'login': 'second-team', 'password': 'strong-other-01'})
        self.assertNotIn('password', created)
        path = BASE + '/companies/1'
        manager = await self.request('POST', path + '/accounts', {
            'role': 'manager', 'login': 'manager-test', 'password': 'manager-test-01', 'extension': '101'})
        cid = created['id']
        await self.request('GET', f"{BASE}/companies/{cid}/accounts/{manager['id']}/permissions", status=404)
        permission = await self.request('PATCH', path + '/permissions/manager', {'version': 0, 'denied': ['deals.edit']})
        self.assertEqual(permission['denied'], ['deals.edit'])
        await self.session(manager['id'], 'manager', extension='101')
        await self.request('GET', '/api/deals')
        await self.request('POST', '/api/deals/79990000001/event', {'kind': 'note'}, status=403)
        await self.session(self.admin)
        await self.request('POST', f"{path}/accounts/{manager['id']}/password", {'password': 'replacement-01'})
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_sessions WHERE invite_id=$1', manager['id']), 0)

    async def test_custom_pipeline_and_two_deliveries_of_same_event(self):
        await self.session(self.admin)
        path = BASE + '/companies/1'
        await self.pool.execute(
            """INSERT INTO deal_events(client_id,phone,kind,extension) VALUES(1,'9990000001','presentation','101')""")
        original = await self.request('GET', '/api/deals/9990000001')
        seed = await self.request('GET', path + '/pipelines/default/versions')
        self.assertEqual(seed['items'][0]['config'], admin_pipelines.default_config())
        config = admin_pipelines.default_config()
        config['stages'].insert(3, {'key': 'consultation', 'name': 'Повторная консультация', 'stall_days': 2})
        config['transitions'] = {'presentation': ['consultation']}
        draft = await self.request('PUT', path + '/pipelines/default/draft', {'version': 0, 'config': config})
        await self.request('GET', path + f"/pipelines/default/preview?version={draft['version']}")
        await self.request('POST', path + '/pipelines/default/publish', {'version': draft['version']})
        current = await self.request('GET', '/api/deals/9990000001')
        self.assertEqual(original['stage'], current['stage'])
        rule = await self.request('POST', path + '/automations', {'version': 0, 'name': 'Follow-up', 'spec': {
            'trigger': {'type': 'deal.stage_changed'},
            'if': [{'field': 'transition.to', 'op': '=', 'value': 'consultation'}],
            'actions': [{'type': 'email.send', 'to': ['team@example.test'], 'subject': 'Follow-up',
                         'text': '{transition.to}'}]}})
        rule_path = path + f"/automations/{rule['id']}"
        data = {'version': rule['version']}
        await self.request('POST', rule_path + '/publish', data, status=409)
        with patch.object(automations.blocks, 'email_send', new_callable=AsyncMock) as delivery:
            delivery.return_value = 'sent'
            example = await self.request('POST', rule_path + '/simulate', data)
            self.assertTrue(example['conditions_pass'])
            delivery.assert_not_awaited()
            await self.request('POST', rule_path + '/publish', data)
            await self.session(None, 'manager', extension='101')
            moved = await self.request('POST', '/api/deals/9990000001/stage',
                                       {'from': 'presentation', 'stage': 'consultation', 'version': draft['version']})
            self.assertEqual(moved['stage_name'], 'Повторная консультация')
            event = await self.pool.fetchrow("SELECT * FROM automation_events WHERE kind='deal.stage_changed'")
            await asyncio.gather(automations.handle(self.pool, event), automations.handle(self.pool, event))
            delivery.assert_awaited_once()
        historical_time = await self.pool.fetchval('SELECT max(changed_at) FROM crm_deal_stage_changes')
        await self.session(self.admin)
        runs = await self.request('GET', rule_path + '/runs')
        self.assertEqual([(r['status'], r['version']) for r in runs['items']], [('ok', rule['version'])])
        updated = admin_pipelines.default_config()
        nxt = await self.request('PUT', path + '/pipelines/default/draft', {'version': draft['version'], 'config': updated})
        impact = await self.request('GET', path + f"/pipelines/default/preview?version={nxt['version']}")
        self.assertEqual(impact['affected'], [{'stage': 'consultation', 'status': 'active', 'count': 1}])
        await self.request('POST', path + '/pipelines/default/publish', {'version': nxt['version']}, status=400)
        await self.request('POST', path + '/pipelines/default/publish',
                           {'version': nxt['version'], 'remap': {'consultation': 'messenger'}})
        self.assertEqual((await self.request('GET', '/api/deals/9990000001'))['stage'], 'messenger')
        report = await self.request('GET', path + '/pipelines/default/report?as_of=' + quote(historical_time.isoformat()))
        self.assertEqual(report['version'], draft['version'])
        self.assertEqual(next(s['reached'] for s in report['funnel']['stages'] if s['key'] == 'consultation'), 1)
        await self.request('POST', rule_path + '/pause', data)
        self.assertTrue((await self.request('POST', rule_path + '/resume', data))['enabled'])

    async def test_home_mango_is_queued_and_credentials_are_write_only(self):
        await self.session(self.admin)
        path = BASE + '/companies/1'
        data = {'key': 'sentinel-mango-credential', 'salt': 'sentinel-private-salt'}
        queued = await self.request('POST', path + '/integrations/mango/check', data, status=202)
        with patch.object(admin_integrations.mango_client, 'call', new_callable=AsyncMock) as probe:
            probe.return_value = {'balance': 10}
            await admin_integrations.process(self.pool)
            probe.assert_awaited_once_with('account/balance', data['key'], data['salt'], {}, timeout=12)
        op = await self.request('GET', path + f"/operations/{queued['id']}")
        self.assertEqual(op['status'], 'ok')
        self.assertNotIn('sentinel', str(op))
        state = await self.request('GET', path + '/integrations')
        self.assertEqual(state['checks']['mango']['status'], 'ok')
        self.assertNotIn('sentinel', str(await self.request('GET', path + '/audit')))

        another = await self.request('POST', path + '/integrations/mango/check', {}, status=202)
        await self.pool.execute(
            "UPDATE crm_admin_operations SET status='running', started_at=now()-interval '6 min' WHERE id=$1",
            another['id'])
        with patch.object(admin_integrations.mango_client, 'call', new_callable=AsyncMock) as probe:
            await admin_integrations.process(self.pool)
            probe.assert_not_awaited()
        unknown = await self.request('GET', path + f"/operations/{another['id']}")
        self.assertEqual(unknown['status'], 'delivery_unknown')

    @unittest.skipUnless(os.getenv('CRM_ADMIN_PLAYWRIGHT'), 'CRM_ADMIN_PLAYWRIGHT is required')
    async def test_browser_workflow_with_real_crm_routes(self):
        await self.pool.execute("""INSERT INTO deal_events(client_id,phone,kind,extension)
                                VALUES(1,'9990000001','presentation','101')""")
        self.enterContext(patch.object(web, 'SECURE_COOKIE', False))
        self.enterContext(patch.object(admin_integrations.mango_client, 'call', AsyncMock(return_value={'balance': 10})))
        send = self.enterContext(patch.object(automations.blocks, 'email_send', AsyncMock(return_value='sent')))
        env = {**os.environ, 'CRM_ADMIN_TEST_URL': str(self.http.make_url('/'))}
        script = Path(__file__).resolve().parents[1] / 'scripts/check_crm_platform_admin_browser.cjs'

        async def work():
            while True:
                await admin_integrations.process(self.pool)
                await automations.tick(self.pool)
                await asyncio.sleep(0.2)

        worker = asyncio.create_task(work())
        process = await asyncio.create_subprocess_exec(
            'node', str(script), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=180)
            self.assertEqual(process.returncode, 0, output.decode(errors='replace'))
            send.assert_awaited_once()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
