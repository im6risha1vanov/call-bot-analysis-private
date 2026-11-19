"""Real CRM routes against an explicitly dedicated loopback PostgreSQL database."""
import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from aiohttp.test_utils import TestClient, TestServer
from tests.test_crm import DBCase, DSN
from crm import auth, web


def safe_dsn():
    if not DSN: return False
    u = urlparse(DSN)
    return (u.scheme in ('postgres', 'postgresql') and u.hostname in ('127.0.0.1', 'localhost', '::1')
            and u.path == '/callbot_methodology_test_pilot_api' and not u.query and not u.fragment)


@unittest.skipUnless(safe_dsn(), 'set METHODOLOGY_TEST_DATABASE_URL to dedicated loopback database')
class PilotHttp(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        if await self.pool.fetchval('SELECT current_database()') != 'callbot_methodology_test_pilot_api':
            raise RuntimeError('Wrong database')
        root = Path(__file__).resolve().parents[1]
        for migration in ('030_company_settings.sql', '045_crm_personal_agent.sql', '056_crm_team_assignments.sql', '057_crm_team_meetings.sql', '059_crm_personal_day.sql'):
            await self.pool.execute((root / 'migrations' / migration).read_text(encoding='utf8'))
        await self.pool.execute('TRUNCATE crm_team_assignments, crm_personal_profiles CASCADE')
        for tg, ext, login in ((123, '101', 'pilot-a'), (124, '102', 'pilot-b'), (901, 'head', 'nonpilot')):
            role = 'director' if tg == 901 else 'manager'
            invite = await self.pool.fetchval('''INSERT INTO crm_invites(token_hash,role,client_id,extension,name,telegram_user_id)
                VALUES($1,$2,1,$3,$4,$5) RETURNING id''', 'invite-'+login, role, ext, login, tg)
            await self.pool.execute('''INSERT INTO crm_sessions(token_hash,expires_at,role,client_id,invite_id,extension,telegram_user_id)
                VALUES($1,now()+interval '1 hour',$2,1,$3,$4,$5)''', auth.digest('session-'+login), role, invite, ext, tg)
        await self.pool.execute("INSERT INTO crm_personal_profiles(telegram_user_id,display_name,timezone) VALUES(123,'Pilot','Europe/Moscow')")
        await self.pool.execute("INSERT INTO crm_team_assignments(company_id,creator_id,assignee_id,title) VALUES(1,123,124,'Тестовое поручение')")
        self.enterContext(patch.dict(os.environ, {'CRM_PERSONAL_PILOT_TELEGRAM_IDS':'123,124','CRM_PERSONAL_DAY_ENABLED':'1','CRM_HOME_COMPANY':'1'}))
        self.http = TestClient(TestServer(web.build(self.pool)))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)

    async def call(self, path, token='session-pilot-a', status=200):
        response = await self.http.get(path, headers={'Cookie':'crm_session='+token, 'X-CRM':'1'})
        self.assertEqual(response.status, status, await response.text())
        return await response.json()

    async def test_real_http_capabilities_people_and_items(self):
        me = await self.call('/api/me')
        self.assertEqual(me['capabilities'], {'team_pilot':True, 'team_day':True, 'team_actor_id':123})
        self.assertEqual((await self.call('/api/team/assignments/people'))['people'], [{'id':124,'display_name':'Второй'}])
        rows = (await self.call('/api/team/assignments?filter=created'))['assignments']
        self.assertEqual([(r['title'],r['version']) for r in rows], [('Тестовое поручение',1)])
        self.assertEqual((await self.call('/api/personal/day'))['timezone'], 'Europe/Moscow')
        other = await self.call('/api/me', 'session-nonpilot')
        self.assertEqual(other['capabilities'], {'team_pilot':False,'team_day':False,'team_actor_id':None})
        await self.call('/api/team/assignments/people', 'session-nonpilot', 403)

    @unittest.skipUnless(os.getenv('PILOT_BROWSER_TEST'), 'set PILOT_BROWSER_TEST=1')
    async def test_browser_with_real_api(self):
        env = {**os.environ, 'PILOT_URL':str(self.http.make_url('/')), 'PILOT_SESSION':'session-pilot-a'}
        script = Path(__file__).parent / 'js/team_pilot_real_api.cjs'
        proc = await asyncio.create_subprocess_exec('node',str(script),env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), timeout=40)
            self.assertEqual(proc.returncode, 0, output.decode(errors='replace'))
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
