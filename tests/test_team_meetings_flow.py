import asyncio, hashlib, json, os, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import patch
import asyncpg
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from crm import team_meetings as m, team_assignments as a
from tools import Forbidden
import importlib.util
spec=importlib.util.spec_from_file_location('recorder_importer','integrations/meeting_recorder/importer.py')
imp=importlib.util.module_from_spec(spec);spec.loader.exec_module(imp)
DSN=os.getenv('MEETING_TEST_DSN')

def person(tg):
 return SimpleNamespace(test=False,support=False,support_by=None,invite_id=1 if tg==101 else 2,role='manager',company=1,actor=SimpleNamespace(telegram_user_id=tg,client_id=1))

@unittest.skipUnless(DSN,'set MEETING_TEST_DSN to a dedicated disposable database')
class Flow(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  parsed=urlsplit(DSN)
  if parsed.scheme not in ('postgresql','postgres') or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.path != '/callbot_methodology_test_meeting_flow' or parsed.query or parsed.fragment:
   raise RuntimeError('MEETING_TEST_DSN must point to the dedicated loopback test database')
  env=patch.dict(os.environ, {'CRM_PERSONAL_PILOT_TELEGRAM_IDS':'101,102','CRM_MEETINGS_IMPORT_TOKEN':'x'*40,'CRM_MEETINGS_SOURCES_JSON':'{"teams_auto":{"company_id":1,"series_id":"weekly","owner_ids":[101]}}'})
  env.start();self.addCleanup(env.stop)
  self.pool=await asyncpg.create_pool(DSN)
  self.addAsyncCleanup(self.pool.close)
  if await self.pool.fetchval('SELECT current_database()') != 'callbot_methodology_test_meeting_flow':
   raise RuntimeError('Unexpected connected database')
  async with self.pool.acquire() as c:
   await c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
   await c.execute('''CREATE TABLE clients(id integer PRIMARY KEY); INSERT INTO clients VALUES(1);
    CREATE TABLE employees(client_id integer, telegram_user_id bigint, archived_at timestamptz);
    CREATE TABLE crm_invites(id integer, client_id integer, telegram_user_id bigint, revoked_at timestamptz);
    INSERT INTO employees VALUES(1,101,NULL),(1,102,NULL); INSERT INTO crm_invites VALUES(1,1,101,NULL),(2,1,102,NULL);''')
   for name in ('056_crm_team_assignments.sql','057_crm_team_meetings.sql'): await c.execute(Path('migrations',name).read_text(encoding='utf8'))
  self.who=person(101)
  async def viewer(req): return self.who
  async def body(req): return await req.json()
  @web.middleware
  async def errors(req,handler):
   try: return await handler(req)
   except Forbidden: raise web.HTTPForbidden()
   except ValueError: raise web.HTTPBadRequest()
  key=web.AppKey('pool');app=web.Application(middlewares=[errors],client_max_size=512*1024);app[key]=self.pool
  app.add_routes(m.routes(viewer,body,key));app.add_routes(a.routes(viewer,body,key))
  self.client=TestClient(TestServer(app,host='127.0.0.1',port=8095));await self.client.start_server()
 async def asyncTearDown(self):
  await self.client.close()
 async def call(self,method,path,data,key):
  return await self.client.request(method,path,json=data,headers={'Idempotency-Key':key})
 async def test_fixture_import_convert_review_agenda(self):
  with tempfile.TemporaryDirectory() as d:
   b=Path(d);sid='20261119T120000Z-1234abcd';event='1234567890123';p=b/'sessions'/sid;p.mkdir(parents=True);(b/'teams'/'events').mkdir(parents=True);n=b/'notifications'/sid;n.mkdir(parents=True)
   def put(path,obj): path.write_text(json.dumps(obj),encoding='utf8')
   put(p/'status.json',{'id':sid,'mode':'meeting','state':'complete','created_at':'2026-11-19T12:00:00Z','teams_event_id':event})
   put(b/'teams'/'events'/(event+'.json'),{'chat_id':imp.CHAT_ID,'message_id':event})
   raw={'session_id':sid,'utterances':[{'source':'x','start':1,'end':2,'text':'decision'}],'errors':[]};put(p/'transcript.json',raw)
   digest=hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True).encode()).hexdigest();(n/('analysis-'+digest[:16])).mkdir()
   put(n/'report.json',{'session_id':sid,'notes':{'decisions':[{'text':'Do it','evidence_ids':[0]}]},'analysis_error':False})
   payload=imp.snapshot(b,sid,{event:{'state':'started','session_id':sid}},0)
   await asyncio.to_thread(imp.send,'http://127.0.0.1:8095/api/internal/team/meetings/import','x'*40,payload)
   await asyncio.to_thread(imp.send,'http://127.0.0.1:8095/api/internal/team/meetings/import','x'*40,payload)
   r=await self.client.get('/api/team/meetings/1');meeting=await r.json();item=meeting['items'][0]['id']
   r=await self.call('POST','/api/team/meetings/1/convert',{'item_id':item,'assignee_id':102},'x');self.assertEqual(r.status,200);task=(await r.json())['assignment']['id']
   r=await self.call('POST','/api/team/meetings/1/convert',{'item_id':item,'assignee_id':102},'x');self.assertTrue((await r.json())['duplicate'])
   r=await self.call('POST','/api/team/meetings/1/convert',{'item_id':item,'assignee_id':101},'x');self.assertEqual(r.status,409)
   self.who=person(102)
   r=await self.call('POST',f'/api/team/assignments/{task}/submit',{'version':1,'result_text':'Done'},'submit');self.assertEqual(r.status,200)
   self.who=person(101)
   r=await self.call('POST',f'/api/team/assignments/{task}/return',{'version':2,'comment':'Fix'},'return');self.assertEqual(r.status,200)
   self.who=person(102)
   r=await self.call('POST',f'/api/team/assignments/{task}/submit',{'version':3,'result_text':'Fixed'},'submit2');self.assertEqual(r.status,200)
   self.who=person(101)
   r=await self.call('POST',f'/api/team/assignments/{task}/accept',{'version':4},'accept');self.assertEqual(r.status,200)
   r=await self.client.get('/api/team/meetings/agenda?series_id=weekly');agenda=await r.json();self.assertEqual(agenda['accepted'][0]['id'],task)
