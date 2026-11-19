import os, unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import patch
import asyncpg
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from crm import team_knowledge
from tools import Forbidden

DSN=os.getenv('KNOWLEDGE_TEST_DSN')
def person(tg,company=1,role='manager'):
 return SimpleNamespace(test=False,support=False,support_by=None,invite_id=tg,role=role,company=company,actor=SimpleNamespace(telegram_user_id=tg,client_id=company))

@unittest.skipUnless(DSN,'set KNOWLEDGE_TEST_DSN to isolated database')
class KnowledgeTest(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  p=urlsplit(DSN)
  if p.scheme not in ('postgres','postgresql') or p.hostname not in ('127.0.0.1','localhost','::1') or p.path!='/callbot_methodology_test_knowledge' or p.query or p.fragment:
   raise RuntimeError('Dedicated local test database required')
  env=patch.dict(os.environ,{'CRM_PERSONAL_PILOT_TELEGRAM_IDS':'101,102,103','CRM_TEAM_KNOWLEDGE_ENABLED':'1'});env.start();self.addCleanup(env.stop)
  self.pool=await asyncpg.create_pool(DSN);self.addAsyncCleanup(self.pool.close)
  if await self.pool.fetchval('SELECT current_database()')!='callbot_methodology_test_knowledge':raise RuntimeError('Wrong database')
  async with self.pool.acquire() as c:
   await c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
   await c.execute('''CREATE TABLE clients(id integer primary key); INSERT INTO clients VALUES(1),(2);
    CREATE TABLE employees(client_id integer,telegram_user_id bigint,archived_at timestamptz);
    CREATE TABLE crm_invites(id integer,client_id integer,telegram_user_id bigint,revoked_at timestamptz);
    INSERT INTO employees VALUES(1,101,NULL),(1,102,NULL),(2,103,NULL);
    INSERT INTO crm_invites VALUES(101,1,101,NULL),(102,1,102,NULL),(103,2,103,NULL);''')
   await c.execute(Path('migrations/060_crm_team_knowledge.sql').read_text(encoding='utf8'))
  self.who=person(101,role='director')
  async def viewer(req):return self.who
  async def body(req):return await req.json()
  @web.middleware
  async def errors(req,handler):
   try:return await handler(req)
   except Forbidden:raise web.HTTPForbidden()
   except ValueError:raise web.HTTPBadRequest()
  key=web.AppKey('pool');app=web.Application(middlewares=[errors]);app[key]=self.pool;app.add_routes(team_knowledge.routes(viewer,body,key))
  self.client=TestClient(TestServer(app));await self.client.start_server();self.addAsyncCleanup(self.client.close)
 async def test_access_search_and_gate(self):
  async def post(path,data):return await self.client.post('/api/team/knowledge/'+path,json=data)
  r=await post('sources',{'title':'Shared source','scope':'shared','provenance_url':'https://example.org/a','source_date':'2026-11-19'});self.assertEqual(r.status,201);source=(await r.json())['source']['id']
  r=await post('documents',{'source_id':source,'title':'General','body':'alpha shared','scope':'shared'});self.assertEqual(r.status,201)
  r=await post('documents',{'source_id':source,'title':'Private','body':'alpha secret'});self.assertEqual(r.status,201);private=(await r.json())['document']['id']
  self.who=person(102)
  r=await self.client.get('/api/team/knowledge/documents?q=alpha');docs=(await r.json())['documents'];self.assertEqual([d['title'] for d in docs],['General']);self.assertEqual(docs[0]['provenance_url'],'https://example.org/a')
  r=await self.client.get(f'/api/team/knowledge/documents/{private}');self.assertEqual(r.status,404)
  r=await post('sources',{'title':'Forbidden','scope':'shared'});self.assertEqual(r.status,403)
  self.who=person(103,company=2)
  r=await self.client.get('/api/team/knowledge/documents?q=alpha');self.assertEqual((await r.json())['documents'],[])
  os.environ['CRM_TEAM_KNOWLEDGE_ENABLED']='0'
  r=await self.client.get('/api/team/knowledge/documents');self.assertEqual(r.status,404)
