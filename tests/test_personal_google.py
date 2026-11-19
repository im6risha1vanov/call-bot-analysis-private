import unittest
from unittest.mock import patch
from crm import personal_google as g

class GoogleFoundationTests(unittest.TestCase):
 def test_separate_minimal_scopes(self):
  for kind in g.SCOPES:
   base={'access_token':'a','refresh_token':'r','token_type':'Bearer','scope':'openid email '+g.SCOPES[kind]}
   g.validate_token(base,kind)
   for scope in ('openid email', base['scope']+' '+g.SCOPES['calendar' if kind=='mail' else 'mail']):
    with self.assertRaises(ValueError): g.validate_token({**base,'scope':scope},kind)
   with self.assertRaises(ValueError): g.validate_token({**base,'refresh_token':''},kind)
 def test_pkce_and_state(self):
  self.assertEqual(g.challenge('abc'),'ungWv48Bz-pBQUDeXa4iI7ADYaOWF3qctBD_YfIAFa0')
  self.assertNotEqual(g.hash_state('a'),g.hash_state('b'))
 def test_config_rejects_unsafe_redirect(self):
  with patch.dict('os.environ',{'CRM_GOOGLE_CLIENT_ID':'id','CRM_GOOGLE_CLIENT_SECRET':'secret','CRM_GOOGLE_REDIRECT_URI':'http://evil/api/personal/google/callback'}):
   with self.assertRaises(ValueError): g.config()

if __name__=='__main__': unittest.main()

# Uses only an explicitly named loopback disposable database and a unique schema.
import os, json, secrets
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import asyncpg

TEST_DSN = os.environ.get('PERSONAL_GOOGLE_TEST_DSN','')

def safe_test_dsn(value):
    p=urlsplit(value)
    return p.scheme in ('postgres','postgresql') and p.hostname in ('127.0.0.1','localhost','::1') and p.path == '/google_test' and not p.query and not p.fragment

@unittest.skipUnless(TEST_DSN and safe_test_dsn(TEST_DSN), 'requires explicit loopback disposable google_test database')
class GoogleDatabaseTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  from cryptography.fernet import Fernet
  self.env=patch.dict(os.environ,{'ENCRYPTION_KEY':Fernet.generate_key().decode(),'CRM_GOOGLE_CLIENT_ID':'test-client','CRM_GOOGLE_CLIENT_SECRET':'test-secret','CRM_GOOGLE_REDIRECT_URI':'https://crm.example/api/personal/google/callback'})
  self.env.start(); self.addCleanup(self.env.stop)
  from crypto_util import _fernet
  _fernet.cache_clear(); self.addCleanup(_fernet.cache_clear)
  self.admin=await asyncpg.connect(TEST_DSN)
  self.schema='t_'+secrets.token_hex(12)
  await self.admin.execute('CREATE SCHEMA "'+self.schema+'"')
  async def init(c): await c.execute('SET search_path TO "'+self.schema+'"')
  self.pool=await asyncpg.create_pool(TEST_DSN,server_settings={'search_path':self.schema})
  await self.pool.execute('CREATE TABLE employees (telegram_user_id bigint,client_id bigint,archived_at timestamptz)')
  await self.pool.execute("INSERT INTO employees VALUES (123,1,NULL),(124,1,NULL),(123,2,NULL)")
  for f in ('049_crm_personal_connections.sql','058_crm_personal_google.sql','061_crm_personal_google_actions.sql'):
   await self.pool.execute((Path(__file__).resolve().parents[1]/'migrations'/f).read_text(encoding='utf-8-sig'))
  await self.pool.execute("INSERT INTO crm_personal_connections(telegram_user_id,client_id,kind,provider,email) VALUES(123,1,'mail','google','a@example.com')")
  async def viewer(request): return SimpleNamespace(actor=SimpleNamespace(telegram_user_id=int(request.headers.get('User','123')),client_id=int(request.headers.get('Company','1'))))
  async def ident(pool,who): return who.actor.telegram_user_id
  self.identity=patch.object(g.personal,'identity',side_effect=ident); self.identity.start(); self.addCleanup(self.identity.stop)
  from crm import personal_google_actions
  app=web.Application(); app['db']=self.pool; app.router.add_routes(g.routes(viewer,'db')); app.router.add_routes(personal_google_actions.routes(viewer,'db'))
  self.client=TestClient(TestServer(app)); await self.client.start_server()
 async def asyncTearDown(self):
  await self.client.close(); await self.pool.close()
  await self.admin.execute('DROP SCHEMA "'+self.schema+'" CASCADE'); await self.admin.close()
 async def start(self):
  response=await self.client.post('/api/personal/google/mail/start'); self.assertEqual(response.status,200)
  return parse_qs(urlsplit((await response.json())['authorization_url']).query)['state'][0]
 def token(self): return {'access_token':'access','refresh_token':'refresh','token_type':'Bearer','scope':'openid email '+g.SCOPES['mail']}
 async def test_callback_success_replay_and_disconnect(self):
  state=await self.start()
  provider=AsyncMock(side_effect=[self.token(),{'sub':'google-1','email':'a@example.com','email_verified':True},{},{}])
  with patch.object(g,'provider_json',provider):
   response=await self.client.get('/api/personal/google/callback',params={'state':state,'code':'code'}); self.assertEqual(response.status,200)
   self.assertNotIn('refresh',await response.text())
   row=await self.pool.fetchrow('SELECT token_enc FROM crm_personal_google_tokens'); self.assertNotIn('refresh',row['token_enc'])
   response=await self.client.get('/api/personal/google/callback',params={'state':state,'code':'code'}); self.assertEqual(response.status,400)
   response=await self.client.delete('/api/personal/google/mail'); self.assertEqual(response.status,200); self.assertTrue((await response.json())['provider_revoked'])
   self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_google_tokens'),0)
 async def test_wrong_user_company_and_identity(self):
  state=await self.start()
  for headers in ({'User':'124'},{'Company':'2'}):
   response=await self.client.get('/api/personal/google/callback',params={'state':state,'code':'code'},headers=headers); self.assertEqual(response.status,400)
  with patch.object(g,'provider_json',AsyncMock(side_effect=[self.token(),{'sub':'x','email':'wrong@example.com','email_verified':True}])):
   response=await self.client.get('/api/personal/google/callback',params={'state':state,'code':'code'}); self.assertEqual(response.status,400)
  self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_google_tokens'),0)
 async def test_refresh_rotation(self):
  from crypto_util import encrypt,decrypt
  await self.pool.execute("INSERT INTO crm_personal_google_tokens VALUES(123,1,'mail',1,'sub','a@example.com',$1,now())",encrypt(json.dumps({'refresh_token':'old'})))
  with patch.object(g,'provider_json',AsyncMock(return_value={'access_token':'new-access','refresh_token':'new-refresh','token_type':'Bearer'})):
   token=await g.refresh_token(self.pool,123,1,'mail')
  self.assertEqual(token,'new-access')
  enc=await self.pool.fetchval('SELECT token_enc FROM crm_personal_google_tokens'); self.assertEqual(json.loads(decrypt(enc))['refresh_token'],'new-refresh')

 async def test_expired_and_changed_draft(self):
  state=await self.start(); await self.pool.execute("UPDATE crm_personal_google_states SET expires_at=now()-interval '1 minute'")
  response=await self.client.get('/api/personal/google/callback',params={'state':state,'code':'c'}); self.assertEqual(response.status,400)
  state=await self.start(); await self.pool.execute('UPDATE crm_personal_connections SET version=version+1')
  response=await self.client.get('/api/personal/google/callback',params={'state':state,'code':'c'}); self.assertEqual(response.status,409)
 async def test_read_paths(self):
  await self.pool.execute("INSERT INTO crm_personal_connections(telegram_user_id,client_id,kind,provider,email) VALUES(123,1,'calendar','google','a@example.com')")
  provider=AsyncMock(side_effect=[{'messages':[{'id':'valid-id'}]}, {'payload':{'headers':[{'name':'Subject','value':'hello'}]},'snippet':'snippet'}, {'items':[{'id':'e1','summary':'meeting','start':{'dateTime':'2026-01-01T10:00:00Z'},'end':{'dateTime':'2026-01-01T11:00:00Z'}}]}, {'calendars':{'primary':{'busy':[{'start':'x','end':'y'}]}}}])
  with patch.object(g,'refresh_token',AsyncMock(return_value='access')), patch.object(g,'read_provider',provider):
   response=await self.client.get('/api/personal/google/mail/search?q=hello'); self.assertEqual(response.status,200); self.assertEqual((await response.json())['messages'][0]['headers']['subject'],'hello')
   params={'from':'2026-01-01T00:00:00Z','to':'2026-01-02T00:00:00Z'}
   response=await self.client.get('/api/personal/google/calendar/events',params=params); self.assertEqual(response.status,200); self.assertEqual(len((await response.json())['events']),1)
   response=await self.client.get('/api/personal/google/calendar/freebusy',params=params); self.assertEqual(response.status,200); self.assertEqual(len((await response.json())['busy']),1)
   response=await self.client.get('/api/personal/google/calendar/events',params={'from':'2026-01-01T00:00:00Z','to':'2027-01-02T00:00:00Z'}); self.assertEqual(response.status,400)
