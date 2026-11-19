import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import asyncpg
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from crm import team_assignments as ta
from tools import Forbidden

DSN = os.environ.get('TEAM_TEST_DSN')

@unittest.skipUnless(DSN, 'set TEAM_TEST_DSN to an isolated PostgreSQL database')
class TeamAssignmentsIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ['CRM_PERSONAL_PILOT_TELEGRAM_IDS'] = '101,102'
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=6)
        async with self.pool.acquire() as c:
            if await c.fetchval('SELECT current_database()') != 'callbot_methodology_test_assignments':
                await self.pool.close()
                raise RuntimeError('Refusing to reset a database other than callbot_methodology_test_assignments')
            await c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
            await c.execute('''CREATE TABLE clients(id integer PRIMARY KEY); INSERT INTO clients VALUES(1),(2);
              CREATE TABLE employees(client_id integer,telegram_user_id bigint,archived_at timestamptz);
              CREATE TABLE crm_invites(id integer,client_id integer,telegram_user_id bigint,revoked_at timestamptz);
              INSERT INTO employees VALUES(1,101,NULL),(1,102,NULL),(2,101,NULL);
              INSERT INTO crm_invites VALUES(1,1,101,NULL),(2,1,102,NULL),(3,2,101,NULL);
              CREATE TABLE crm_personal_profiles(id bigint PRIMARY KEY, telegram_user_id bigint);
              CREATE TABLE crm_personal_tasks(id bigint PRIMARY KEY, owner_id bigint, title text);
              INSERT INTO crm_personal_profiles VALUES(1,101),(2,102);
              INSERT INTO crm_personal_tasks VALUES(1,1,'private-a'),(2,2,'private-b');''')
            await c.execute(Path('migrations/056_crm_team_assignments.sql').read_text(encoding='utf-8'))
        self.who = SimpleNamespace(test=False,support=False,support_by=None,invite_id=1,role='manager',company=1,actor=SimpleNamespace(telegram_user_id=101,client_id=1))
        async def viewer(request): return self.who
        async def body(request): return await request.json()
        self.key = web.AppKey('pool')
        @web.middleware
        async def errors(request, handler):
            try: return await handler(request)
            except Forbidden: raise web.HTTPForbidden()
            except ValueError: raise web.HTTPBadRequest()
        app = web.Application(middlewares=[errors]); app[self.key] = self.pool; app.add_routes(ta.routes(viewer, body, self.key))
        self.client = TestClient(TestServer(app)); await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close(); await self.pool.close()
    async def call(self, method, path, data=None, key='key'):
        return await self.client.request(method,path,json=data,headers={'Idempotency-Key':key})
    async def test_retry_concurrency_tenant_and_review(self):
        async def create():
            async with self.pool.acquire() as c, c.transaction():
                return await ta.create(c,(1,101),{'title':'Task','assignee_id':102},idempotency_key='same')
        a,b = await asyncio.gather(create(),create())
        self.assertEqual(a['id'],b['id'])
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_team_assignments'),1)
        r = await self.call('POST',f"/api/team/assignments/{a['id']}/accept",{'version':1},'accept')
        self.assertEqual(r.status,409)
        self.who = SimpleNamespace(test=False,support=False,support_by=None,invite_id=2,role='manager',company=1,actor=SimpleNamespace(telegram_user_id=102,client_id=1))
        r = await self.call('POST',f"/api/team/assignments/{a['id']}/submit",{'version':1,'result_text':'Done','result_links':['https://example.com']},'submit')
        self.assertEqual(r.status,200)
        r = await self.call('POST',f"/api/team/assignments/{a['id']}/accept",{'version':2},'bad')
        self.assertEqual(r.status,403)
        r = await self.client.get('/api/team/mine'); result = await r.json()
        self.assertEqual([i['title'] for i in result['items'] if i['kind']=='personal'], ['private-b'])
        self.who = SimpleNamespace(test=False,support=False,support_by=None,invite_id=3,role='manager',company=2,actor=SimpleNamespace(telegram_user_id=101,client_id=2))
        r = await self.client.get(f"/api/team/assignments/{a['id']}"); self.assertEqual(r.status,404)
    async def test_stale_version(self):
        async with self.pool.acquire() as c, c.transaction():
            a = await ta.create(c,(1,101),{'title':'Task','assignee_id':102},idempotency_key='create')
        async def edit(key):
            return await self.call('PATCH',f"/api/team/assignments/{a['id']}",{'version':1,'title':key},key)
        responses = await asyncio.gather(edit('one'),edit('two'))
        self.assertEqual(sorted(r.status for r in responses),[200,409])
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_team_assignment_history'),2)
    async def test_blocker_preserves_state_and_review_only_creator(self):
        async with self.pool.acquire() as c, c.transaction():
            a = await ta.create(c,(1,101),{'title':'Task','assignee_id':102},idempotency_key='create')
        task = a['id']
        self.who = SimpleNamespace(test=False,support=False,support_by=None,invite_id=2,role='manager',company=1,actor=SimpleNamespace(telegram_user_id=102,client_id=1))
        r = await self.call('POST',f'/api/team/assignments/{task}/block',{'version':1,'blocker':'Waiting'},'block')
        item = (await r.json())['assignment']; self.assertEqual((item['status'], item['blocker']),('assigned','Waiting'))
        r = await self.call('POST',f'/api/team/assignments/{task}/clear_blocker',{'version':2},'clear')
        item = (await r.json())['assignment']; self.assertEqual((item['status'], item['blocker']),('assigned',''))
        r = await self.call('POST',f'/api/team/assignments/{task}/submit',{'version':3,'result_text':'Done'},'submit')
        self.assertEqual(r.status,200)
        self.who = SimpleNamespace(test=False,support=False,support_by=None,invite_id=1,role='manager',company=1,actor=SimpleNamespace(telegram_user_id=101,client_id=1))
        r = await self.call('POST',f'/api/team/assignments/{task}/accept',{'version':4},'accept')
        self.assertEqual((await r.json())['assignment']['status'],'accepted')
        r = await self.call('POST',f'/api/team/assignments/{task}/accept',{'version':4},'accept')
        self.assertEqual(r.status,200)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_team_assignment_history'),5)

    async def test_deal_redaction_and_retry_after_reassignment(self):
        await self.pool.execute("ALTER TABLE employees ADD COLUMN role text DEFAULT 'manager', ADD COLUMN extension text")
        allowed = {101, 102}
        async def check_deal(db, actor, phone):
            if actor.telegram_user_id not in allowed: raise Forbidden('No access')
            return {'phone': phone}
        with patch.object(ta.deals, 'one', check_deal):
            r = await self.call('POST','/api/team/assignments',{'title':'Shared','assignee_id':102,'deal_id':'9991234567'},'create-deal')
            self.assertEqual(r.status,201); task = (await r.json())['assignment']['id']
            allowed.remove(101)
            for path in ['/api/team/assignments?filter=created',f'/api/team/assignments/{task}',f'/api/team/assignments/{task}/history']:
                r = await self.client.get(path); data = await r.json()
                if 'assignments' in data: item = data['assignments'][0]
                elif 'history' in data: item = data['history'][0]['snapshot']
                else: item = data['assignment']
                self.assertIsNone(item['deal_id'])
            r = await self.call('POST','/api/team/assignments',{'title':'Shared','assignee_id':102,'deal_id':'9991234567'},'create-deal')
            self.assertIsNone((await r.json())['assignment']['deal_id'])
            self.who = SimpleNamespace(test=False,support=False,support_by=None,invite_id=2,role='manager',company=1,actor=SimpleNamespace(telegram_user_id=102,client_id=1))
            r = await self.call('POST',f'/api/team/assignments/{task}/start',{'version':1},'start')
            self.assertEqual(r.status,200)
            allowed.remove(102)
            r = await self.client.get('/api/team/mine'); self.assertIsNone((await r.json())['items'][0]['deal_id'])
            r = await self.call('POST',f'/api/team/assignments/{task}/start',{'version':1},'start')
            self.assertIsNone((await r.json())['assignment']['deal_id'])
            # Reassignment is made by an administrator in this fixture; retry must recheck current membership.
            await self.pool.execute('UPDATE crm_team_assignments SET assignee_id=103 WHERE id=$1',task)
            r = await self.call('POST',f'/api/team/assignments/{task}/start',{'version':1},'start')
            self.assertEqual(r.status,404)
            await self.pool.execute('UPDATE employees SET archived_at=now() WHERE client_id=1 AND telegram_user_id=102')
            r = await self.call('POST',f'/api/team/assignments/{task}/start',{'version':1},'start')
            self.assertEqual(r.status,403)

    async def test_return_comment_separate_from_blocker(self):
        async with self.pool.acquire() as c, c.transaction():
            item = await ta.create(c,(1,101),{'title':'Task','assignee_id':102},idempotency_key='new')
        task = item['id']
        self.who = SimpleNamespace(test=False,support=False,support_by=None,invite_id=2,role='manager',company=1,actor=SimpleNamespace(telegram_user_id=102,client_id=1))
        await self.call('POST',f'/api/team/assignments/{task}/block',{'version':1,'blocker':'Waiting'},'block')
        r = await self.call('POST',f'/api/team/assignments/{task}/submit',{'version':2,'result_text':'Done'},'submit')
        self.assertEqual((await r.json())['assignment']['blocker'],'')
        self.who = SimpleNamespace(test=False,support=False,support_by=None,invite_id=1,role='manager',company=1,actor=SimpleNamespace(telegram_user_id=101,client_id=1))
        r = await self.call('POST',f'/api/team/assignments/{task}/return',{'version':3,'comment':''},'return-bad')
        self.assertEqual(r.status,400)
        r = await self.call('POST',f'/api/team/assignments/{task}/return',{'version':3,'comment':'Please revise'},'return')
        item = (await r.json())['assignment']; self.assertEqual((item['blocker'],item['review_note']),('','Please revise'))
        hist = await self.client.get(f'/api/team/assignments/{task}/history')
        self.assertEqual((await hist.json())['history'][-1]['snapshot']['review_note'],'Please revise')
