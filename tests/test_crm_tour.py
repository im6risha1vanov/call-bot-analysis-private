"""Normal-account guidance safety, no real DB/network/providers."""
import ast,json,os,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from crm import tour
ROOT=Path(__file__).resolve().parents[1]
def who(role='manager',company=1):return SimpleNamespace(role=role,actor=SimpleNamespace(client_id=company) if role!='client' else None,company=company)
class PolicyTests(unittest.TestCase):
 def test_role_company_and_module_context(self):
  for role in ['manager','director']:
   data=tour.context(who(role),{'id':1,'modules':[]});self.assertEqual(data['role'],role);self.assertEqual(data['progress'],'not_stored');self.assertTrue(data['tabs']);self.assertNotIn('records_data',data)
   self.assertFalse(any(t.get('module') for t in data['tabs']))
  for role in ['client','guest']:
   with self.assertRaises(web.HTTPForbidden):tour.context(who(role),{'id':1,'modules':[]})
  with self.assertRaises(web.HTTPForbidden):tour.context(who(),{'id':2,'modules':[]})
 def test_closed_write_and_external_capability_policy(self):
  for path in ['/api/dial','/api/outcome','/api/tasks/123','/api/deals/123/event','/api/contracts/register','/api/gpt','/api/automations/1/test','/api/robot/call','/hook/anything','/api/future-paid-operation']:
   for method in ['POST','PUT','PATCH','DELETE']:self.assertFalse(tour.permitted(method,path),(method,path))
  for path in ['/api/webphone','/webphone/ws','/api/contracts/lookup/inn/1111111111','/api/robot/voices','/api/robot/voices/sample','/api/future-paid-operation']:self.assertFalse(tour.permitted('GET',path),path)
  for path in ['/api/card','/api/queue','/api/review/123','/api/deals/123','/api/reports','/api/robot/cab/campaigns']:self.assertTrue(tour.permitted('GET',path),path)
 def test_scripts_do_not_store_records_progress_or_fixtures(self):
  for name in ['guide.js','tour-guard.js']:
   source=(ROOT/'crm/static/learning'/name).read_text()
   for forbidden in ['localStorage','sessionStorage','AveronClassroom','fixtures(','/api/outcome\', {method:']:self.assertNotIn(forbidden,source)
  data=json.loads((ROOT/'crm/learning_guide.json').read_text());examples=json.loads((ROOT/'crm/learning_examples.json').read_text())
  for tabs in data.values():
   for tab in tabs:
    for step in tab['steps']:
     self.assertNotIn('expected',step);self.assertIn(step.get('example'),examples);self.assertNotIn('Борис',step['text']);self.assertNotIn('УЧ-001',step['text'])
 def test_operational_handlers_unchanged(self):
  import subprocess
  before=ast.parse(subprocess.check_output(['git','show','0ac0a55:crm/web.py'],cwd=ROOT,text=True));after=ast.parse((ROOT/'crm/web.py').read_text())
  old={n.name:ast.dump(n) for n in before.body if isinstance(n,ast.AsyncFunctionDef)};new={n.name:ast.dump(n) for n in after.body if isinstance(n,ast.AsyncFunctionDef)}
  for name in ['api_dial','api_outcome','api_task_change','api_deal_event','api_contracts_register','api_training_assign','api_robot_call']:self.assertEqual(old[name],new[name],name)
class ServerTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  env=patch.dict(os.environ,{'BOT_TOKEN':'123456:synthetic-test-token','TRAIN_BOT_TOKEN':'123456:synthetic-test-token','CVC_API_KEY':'synthetic-test-key','ANTHROPIC_API_KEY':'synthetic-test-key'},clear=True);dotenv=patch('dotenv.load_dotenv',return_value=False);env.start();dotenv.start();self.addCleanup(env.stop);self.addCleanup(dotenv.stop)
  import crm.web as module
  self.mod=module;self.pool=AsyncMock();self.app=web.Application();self.app[module.POOL]=self.pool
 def request(self,method,path,on=True):return make_mocked_request(method,path,headers={'X-CRM':'1',**({'Cookie':tour.COOKIE+'=on'} if on else {})},app=self.app)
 async def test_mode_cookie_blocks_before_any_operational_handler(self):
  for method,path in [('POST','/api/dial'),('POST','/api/outcome'),('POST','/api/contracts/register'),('DELETE','/api/leads/2'),('GET','/api/webphone'),('GET','/webphone/ws'),('GET','/api/future-paid-operation'),('GET','/api/contracts/lookup/inn/1111111111')]:
   handler=AsyncMock(return_value=web.json_response({'unexpected':True}));response=await self.mod.guard(self.request(method,path),handler);self.assertEqual(response.status,403);handler.assert_not_awaited()
  self.pool.execute.assert_not_awaited();self.pool.fetch.assert_not_awaited()
 async def test_safe_read_still_uses_existing_authorization_handler(self):
  handler=AsyncMock(return_value=web.json_response({'normal':True}))
  for on in [True,False]:
   response=await self.mod.guard(self.request('GET','/api/card',on),handler);self.assertEqual(response.status,200)
  self.assertEqual(handler.await_count,2)
 async def test_other_tab_cannot_write_and_unknown_cookie_fails_closed(self):
  request=make_mocked_request('POST','/api/outcome',headers={'X-CRM':'1','Cookie':tour.COOKIE+'=invalid'},app=self.app);handler=AsyncMock();response=await self.mod.guard(request,handler);self.assertEqual(response.status,403);handler.assert_not_awaited()
 async def test_mode_toggle_needs_actor_and_sets_only_a_cookie(self):
  for role in ['manager','director']:
   with patch.object(self.mod,'viewer',AsyncMock(return_value=who(role))),patch.object(self.mod,'body',AsyncMock(return_value={'enabled':True})):
    response=await self.mod.api_learning_mode(self.request('POST','/api/learning/mode',False))
   self.assertEqual(response.cookies[tour.COOKIE].value,'on');self.assertTrue(response.cookies[tour.COOKIE]['httponly']);self.assertEqual(response.cookies[tour.COOKIE]['path'],'/')
  with patch.object(self.mod,'viewer',AsyncMock(return_value=who('client'))):
   with self.assertRaises(web.HTTPForbidden):await self.mod.api_learning_mode(self.request('POST','/api/learning/mode'))
  with patch.object(self.mod,'viewer',AsyncMock(return_value=who())),patch.object(self.mod,'body',AsyncMock(return_value={'enabled':False})):
   response=await self.mod.api_learning_mode(self.request('POST','/api/learning/mode'))
  self.assertEqual(str(response.cookies[tour.COOKIE]['max-age']),'0');self.pool.execute.assert_not_awaited()
 async def test_guide_checks_role_before_loading_company(self):
  with patch.object(self.mod,'viewer',AsyncMock(return_value=who('client'))),patch.object(self.mod.company,'info',AsyncMock()) as info:
   with self.assertRaises(web.HTTPForbidden):await self.mod.api_learning_guide(self.request('GET','/api/learning/guide'))
  info.assert_not_awaited()
class DemoGuideTests(unittest.IsolatedAsyncioTestCase):
 async def call(self,method,path,cookie='learning_demo=manager; crm_guide_demo=1',data=None):
  from scripts import learning_demo
  app=learning_demo.build();request=make_mocked_request(method,path,headers={'Cookie':cookie,'X-CRM':'1','Content-Type':'application/json'},app=app)
  if data is not None:request._read_bytes=json.dumps(data).encode()
  request._match_info=await app.router.resolve(request)
  return await learning_demo.isolated(request,request._match_info.handler)
 async def test_review_link_uses_ordinary_account_screen_and_same_mode_controls(self):
  response=await self.call('GET','/guide/manager');self.assertEqual(response.headers['Location'],'/#learning');self.assertIn('crm_guide_demo',response.cookies)
  response=await self.call('GET','/api/me');data=json.loads(response.text);self.assertFalse(data['test']);self.assertFalse(data['readonly']);self.assertFalse(data['learning_mode'])
  response=await self.call('POST','/api/learning/mode',data={'enabled':True});self.assertEqual(response.cookies[tour.COOKIE].value,'on')
  response=await self.call('GET','/api/me',cookie='learning_demo=manager; crm_guide_demo=1; '+tour.COOKIE+'=on');self.assertTrue(json.loads(response.text)['learning_mode'])
 async def test_bench_never_changes_records_even_with_mode_off(self):
  for active in [False,True]:
   for path in ['/api/dial','/api/outcome','/api/contracts/register']:
    response=await self.call('POST',path,cookie='learning_demo=manager; crm_guide_demo=1'+('; '+tour.COOKIE+'=on' if active else ''),data={});self.assertEqual(response.status,403)
 async def test_both_role_contexts_and_real_ui_reads_use_only_fixture_data(self):
  for role in ['manager','director']:
   cookie='learning_demo='+role+'; crm_guide_demo=1; '+tour.COOKIE+'=on'
   response=await self.call('GET','/api/learning/guide',cookie);self.assertEqual(json.loads(response.text)['role'],role)
   response=await self.call('GET','/api/card?number=00000000001',cookie);self.assertEqual(response.status,200);self.assertIn('calls',json.loads(response.text))
   response=await self.call('GET','/api/queue',cookie);self.assertTrue(json.loads(response.text)['items'])
if __name__=='__main__':unittest.main()
