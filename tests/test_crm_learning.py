"""Network-free learning acceptance checks; no production DB, secrets or models."""
from __future__ import annotations
import ctypes
import ctypes.util
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from crm import learning
from scripts import learning_demo

ROOT = Path(__file__).resolve().parents[1]


def who(role='manager', company=1, account=101):
    actor = SimpleNamespace(client_id=company, telegram_user_id=account, extension='101') if role != 'client' else None
    return SimpleNamespace(role=role, company=company, actor=actor, invite_id=account, test=False, support_by=None)


class JavaScriptVM:
    """Duktape is used for actual ES5 controller code, not to emulate Chromium."""
    def __init__(self):
        lib = ctypes.util.find_library('duktape')
        if not lib:
            raise unittest.SkipTest('Duktape unavailable; run tests/js/learning_test.js with a JS engine')
        self.lib = ctypes.CDLL(lib)
        self.lib.duk_create_heap.argtypes = [ctypes.c_void_p] * 5
        self.lib.duk_create_heap.restype = ctypes.c_void_p
        self.lib.duk_destroy_heap.argtypes = [ctypes.c_void_p]
        self.lib.duk_get_global_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.lib.duk_push_lstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        self.lib.duk_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.duk_safe_to_lstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
        self.lib.duk_safe_to_lstring.restype = ctypes.c_void_p
        self.lib.duk_pop.argtypes = [ctypes.c_void_p]
        self.ctx = self.lib.duk_create_heap(None, None, None, None, None)
        if not self.ctx:
            raise RuntimeError('Cannot create isolated JavaScript heap')

    def evaluate(self, code):
        data = code.encode()
        self.lib.duk_get_global_string(self.ctx, b'eval')
        self.lib.duk_push_lstring(self.ctx, data, len(data))
        result = self.lib.duk_pcall(self.ctx, 1)
        size = ctypes.c_size_t()
        pointer = self.lib.duk_safe_to_lstring(self.ctx, -1, ctypes.byref(size))
        text = ctypes.string_at(pointer, size.value).decode(errors='replace')
        self.lib.duk_pop(self.ctx)
        if result:
            raise AssertionError(text)
        return text

    def close(self):
        self.lib.duk_destroy_heap(self.ctx)


class CatalogTests(unittest.TestCase):
    def test_two_complete_programs_and_exact_source_routes(self):
        data = learning.catalog()['lessons']
        self.assertEqual([l['id'] for l in data if l['role'] == 'manager'], ['m1','m2','m3','m4','m5'])
        self.assertEqual([l['id'] for l in data if l['role'] == 'director'], ['d1','d2','d3','d4','d5','d6'])
        self.assertEqual(sum(l['kind'] == 'practice' for l in data), 10)
        pages = (ROOT/'crm/static/app.js').read_text()
        targets = ''.join(f.read_text() for f in (ROOT/'crm/static').rglob('*.js') if '.min.' not in f.name)
        for lesson in data:
            self.assertTrue(lesson['guide'])
            for step in lesson['guide']:
                self.assertIn(step['page'] + ':', pages)
                self.assertTrue(step['target'] in targets, 'Missing guide target: ' + step['target'])

    def test_floating_copy_does_not_change_lesson_checks_or_content(self):
        import subprocess
        baseline = json.loads(subprocess.check_output(['git', 'show', '9bc7669:crm/learning_lessons.json'], cwd=ROOT, text=True))
        current = learning.catalog()
        for data in (baseline, current):
            for lesson in data['lessons']:
                for step in lesson['steps']: step.pop('hint', None)
        self.assertEqual(current, baseline)

    def test_guided_programme_by_role_and_enabled_tabs_without_quizzes(self):
        data=learning.context(who(), {'modules':['contracts']}, 'Europe/Moscow')
        self.assertEqual([t['id'] for t in data['programme']], ['queue','callbacks','deals','contracts','calls','reports'])
        disabled=learning.context(who(), {'modules':[]}, 'Europe/Moscow')
        self.assertNotIn('contracts', [t['id'] for t in disabled['programme']])
        director=learning.context(who('director'), {'modules':[]}, 'Europe/Moscow')
        self.assertEqual([t['id'] for t in director['programme']], ['summary','calls','callbacks','deals','reports','staff','settings'])
        for packet in (data,disabled,director):
            for tab in packet['programme']:
                self.assertTrue(tab['description'])
                for step in tab['steps']:
                    self.assertNotIn('quiz',step)
                    self.assertFalse(step['action'].startswith('answer_'))
                    self.assertTrue(step['explain'])

    def test_illustrative_gifs_and_posters_decode_without_external_services(self):
        import shutil,subprocess,struct
        examples=json.loads((ROOT/'crm/learning_examples.json').read_text())
        self.assertEqual(len(examples),41)
        ffmpeg=shutil.which('ffmpeg')
        self.assertIsNotNone(ffmpeg, 'Need local ffmpeg to decode actual media; do not mark this check passed without it.')
        for key,item in examples.items():
            for name in ('gif','poster'):
                path=ROOT/'crm/static/learning/examples'/item[name]
                content=path.read_bytes()
                self.assertGreater(len(content),100)
                if name=='gif':
                    self.assertIn(content[:6],(b'GIF87a',b'GIF89a'))
                    self.assertEqual(struct.unpack('<HH',content[6:10]),(item['width'],item['height']))
                else:
                    self.assertEqual(content[:8],b'\x89PNG\r\n\x1a\n')
                decoded=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-threads','1','-i',str(path),'-f','null','-'],capture_output=True,timeout=10)
                self.assertEqual(decoded.returncode,0,key+' '+name+' failed to decode')
            self.assertIn('Схематический',item['alt'])

    def test_form_example_fields_match_current_controls(self):
        examples=json.loads((ROOT/'crm/learning_examples.json').read_text())
        for key in ('save_outcome_callback','move_task','save_note','prepare_contract'):
            item=examples[key]
            self.assertGreaterEqual(item['seconds'],8)
            self.assertGreater(item['poster_time'],max(f['end'] for f in item['fields']))
            source='\n'.join((ROOT/path.split(':')[0]).read_text() for path in item['source'])
            for field in item['fields']:
                selector=field['selector']
                if selector.startswith('#'):
                    self.assertIn(selector[1:],source)
                elif 'data-' in selector:
                    self.assertIn(selector[1:-1],source)
                else:
                    self.assertIn('type="'+('date' if 'date' in selector else 'time')+'"',source)
            self.assertIn(item['sequence'][-2],source)
        callback=examples['save_outcome_callback']
        self.assertEqual([f['label'] for f in callback['fields']],['Дата','Время','Комментарий'])
        self.assertEqual(callback['fields'][2]['value'],'Обсудить предложение')
        self.assertEqual(callback['sequence'][0],'Перезвонить')
        self.assertEqual(callback['sequence'][-2],'Сохранить')

    def test_callback_gif_contains_different_typing_and_saved_frames(self):
        import subprocess,hashlib
        path=ROOT/'crm/static/learning/examples/save_outcome_callback.gif'
        frames=[]
        for second in (2.5,4.25,6,9.25,11):
            decoded=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-threads','1','-ss',str(second),'-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,timeout=10,check=True)
            self.assertEqual(len(decoded.stdout),480*360*3)
            frames.append(hashlib.sha256(decoded.stdout).hexdigest())
        self.assertEqual(len(set(frames)),5,'Empty, date, time, comment and saved result must differ')

    def test_no_duplicate_highlight_attributes(self):
        import re
        for f in (ROOT/'crm/static').rglob('*.js'):
            if '.min.' in f.name: continue
            for tag in re.findall(r'<[^>]+>', f.read_text()):
                self.assertLessEqual(tag.count('data-learning-target='), 1, str(f))

    def test_roles_company_and_module_restrictions(self):
        for role in ('client', 'guest'):
            with self.assertRaises(web.HTTPForbidden):
                learning.context(who(role), {'modules':['contracts']}, 'Europe/Moscow')
        with self.assertRaises(web.HTTPForbidden):
            learning.context(who(), {'modules':['contracts']}, 'Europe/Moscow', 'd1')
        with self.assertRaises(web.HTTPForbidden):
            learning.context(who(), {'modules':[]}, 'Europe/Moscow', 'm4')
        with self.assertRaises(web.HTTPForbidden):
            learning.context(who(), {'id':2,'modules':[]}, 'Europe/Moscow')
        owner = who(); owner.actor.client_id = 2
        with self.assertRaises(web.HTTPForbidden):
            learning.context(owner, {'modules':[]}, 'Europe/Moscow')
        c = learning.context(who('director'), {'modules':[]}, 'Europe/Moscow')
        tour = next(l for l in c['lessons'] if l['id'] == 'd6')
        self.assertEqual(len(tour['guide']),3)
        self.assertEqual(tour['omitted_guide_modules'], ['automations','robot'])

    def test_readonly_work_does_not_disable_fictional_practice(self):
        for role in ('manager', 'director'):
            account = who(role); account.test = True
            data = learning.context(account, {'id': 1, 'modules': ['contracts']}, 'Europe/Moscow')
            self.assertTrue(data['work_readonly'])
            self.assertTrue(data['practice_allowed'])
            self.assertTrue(all(l['available'] for l in data['lessons']))
        self.assertFalse(learning.context(who(), {'id': 1, 'modules': []}, 'Europe/Moscow')['work_readonly'])

    def test_progress_scopes_differ_by_account_company_role(self):
        scopes = {learning.context(who(role, company, account), {'modules':[]}, 'Europe/Moscow')['scope']
                  for role,company,account in [('manager',1,101),('manager',1,102),('manager',2,101),('director',1,101)]}
        self.assertEqual(len(scopes),4)

    def test_clock_is_frozen_and_converts_moscow_to_crm_timezone(self):
        at = datetime(2026,11,4,21,30,tzinfo=timezone.utc)
        msk = learning.clock_for('Europe/Moscow',at)
        self.assertEqual(msk['today'],'2026-11-05')
        self.assertEqual(msk['callback']['due_date'],'2026-11-06')
        self.assertEqual(msk['callback']['due_time'],'15:00')
        berlin = learning.clock_for('Europe/Berlin',at)
        self.assertEqual(berlin['today'],'2026-11-04')
        self.assertEqual(berlin['callback']['due_time'],'13:00')
        self.assertEqual(berlin['move']['due_time'],'09:30')
        with self.assertRaises(ValueError): learning.clock_for('Europe/Moscow',datetime(2026,11,4))

    def test_sandbox_has_no_production_api_fallback(self):
        html = (ROOT/'crm/static/learning/practice.html').read_text()
        script = (ROOT/'crm/static/learning/practice.js').read_text()
        self.assertNotIn('/static/app.js',html)
        for bad in ('fetch(', 'XMLHttpRequest(', 'WebSocket(', '/api/outcome', '/api/contracts', '/api/dial', 'eval(', 'sendBeacon('):
            self.assertNotIn(bad,script)
        csp = learning.practice_response().headers['Content-Security-Policy']
        for part in ("connect-src 'none'", "form-action 'none'", 'sandbox allow-scripts', "frame-ancestors 'self'"):
            self.assertIn(part,csp)
        self.assertNotIn('allow-same-origin',csp)
        self.assertEqual(learning.practice_response().headers['X-Frame-Options'],'SAMEORIGIN')
        self.assertNotIn('<iframe',(ROOT/'crm/static/learning/learning.js').read_text())
        self.assertIn("connect-src 'none'", learning.WORKSPACE_CSP)

    def test_workspace_bootstrap_roundtrip_escapes_html_and_resets_only_on_entry(self):
        import re
        data = learning.context(who(), {'id': 1, 'modules': []}, 'Europe/Moscow')
        data['lessons'][0]['title'] = '</script><script>bad()</script> & пример'
        def packet(response):
            match = re.search(r'id="learning-workspace-context">(.*?)</script>', response.text, re.S)
            return json.loads(match[1])
        first = packet(learning.workspace_response(data, 'first', 'session-one'))
        same = packet(learning.workspace_response(data, 'first', 'session-one'))
        next_entry = packet(learning.workspace_response(data, 'next', 'session-one'))
        next_login = packet(learning.workspace_response(data, 'first', 'session-two'))
        self.assertEqual(first['lessons'][0]['title'], data['lessons'][0]['title'])
        self.assertEqual(first['entry'], same['entry'])
        self.assertNotEqual(first['entry'], next_entry['entry'])
        self.assertNotEqual(first['entry'], next_login['entry'])

    def test_actual_engine_and_practice_ui_all_ten_lessons(self):
        vm = JavaScriptVM()
        try:
            vm.evaluate((ROOT/'crm/static/presentation.js').read_text())
            for name in ('engine.js','progress.js'):
                vm.evaluate((ROOT/'crm/static/learning'/name).read_text())
            clock = learning.clock_for('Europe/Moscow',datetime(2026,11,4,10,tzinfo=timezone.utc))
            vm.evaluate('var TEST_CATALOG = '+json.dumps(learning.catalog(),ensure_ascii=False)+';\nvar TEST_CLOCK = '+json.dumps(clock)+';\nvar TEST_PRACTICE_SOURCE = '+json.dumps((ROOT/'crm/static/learning/practice.js').read_text(),ensure_ascii=False)+';')
            result = json.loads(vm.evaluate((ROOT/'tests/js/learning_test.js').read_text()))
            self.assertEqual(len(result['lessons']),10)
            self.assertEqual(result['uiLessons'],10)
            self.assertEqual(result['networkCalls'],0)
            (Path('/tmp')/'averon-learning-js-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        finally:
            vm.close()


class DemoRouterTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, method, path, role='manager'):
        app = learning_demo.build()
        req = make_mocked_request(method,path,headers={'Cookie':'learning_demo='+role},app=app)
        match = await app.router.resolve(req)
        req._match_info = match
        return await learning_demo.isolated(req,match.handler)

    async def test_demo_both_roles_catalog_and_practice(self):
        for role,lesson in [('manager','m1'),('director','d5')]:
            response = await self.request('GET','/api/learning/context',role)
            self.assertEqual(json.loads(response.text)['role'],role)
            response = await self.request('GET','/learning/practice?lesson='+lesson,role)
            self.assertEqual(response.status,302)
            self.assertEqual(response.headers['Location'],'/#learning')

    async def test_demo_declares_work_readonly_and_allows_local_practice(self):
        for role in ('manager', 'director'):
            response = await self.request('GET', '/api/me', role)
            me = json.loads(response.text)
            self.assertTrue(me['readonly'])
            self.assertFalse(me['can_dial'])
            response = await self.request('GET', '/api/learning/context', role)
            data = json.loads(response.text)
            self.assertTrue(data['work_readonly'])
            self.assertTrue(data['practice_allowed'])

    async def test_direct_links_client_wrong_role_and_disabled_module(self):
        for role,lesson in [('manager','d1'),('client','m1'),('manager-base','m4')]:
            with self.assertRaises(web.HTTPForbidden):
                await self.request('GET','/learning/practice?lesson='+lesson,role)

    async def test_all_working_mutations_are_blocked_even_after_failure(self):
        for method,path in [('POST','/api/outcome'),('POST','/api/tasks/1'),('POST','/api/deals/123/event'),('POST','/api/contracts/register'),('POST','/api/dial'),('POST','/api/gpt'),('POST','/api/robot/call'),('POST','/api/automations'),('DELETE','/api/leads/1')]:
            response = await self.request(method,path,'director')
            self.assertEqual(response.status,403,path)
        response = await self.request('GET','/api/learning/context?lesson=m2')
        self.assertEqual(response.status,200)

    async def test_static_assets_are_not_caught_by_api_deny_route(self):
        app = learning_demo.build()
        req = make_mocked_request('GET','/static/learning/engine.js',app=app)
        match = await app.router.resolve(req)
        self.assertEqual(match.http_exception,None)
        self.assertEqual(match.route.resource.get_info()['prefix'],'/static')

class ProductionHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # No .env read or provider connection, including during module imports.
        self.env = patch.dict(os.environ, {'BOT_TOKEN':'123456:synthetic-test-token', 'TRAIN_BOT_TOKEN':'123456:synthetic-test-token', 'CVC_API_KEY':'synthetic-test-key', 'ANTHROPIC_API_KEY':'synthetic-test-key'}, clear=True)
        self.dotenv = patch('dotenv.load_dotenv', return_value=False)
        self.env.start(); self.dotenv.start()
        self.addCleanup(self.env.stop); self.addCleanup(self.dotenv.stop)
        import crm.web as production
        self.production = production
        self.pool = AsyncMock()
        self.app = web.Application()
        self.app[production.POOL] = self.pool

    async def test_old_practice_links_redirect_to_normal_learning_for_both_roles(self):
        mod=self.production
        for role,lesson in [('manager','m2'),('director','d3')]:
            req=make_mocked_request('GET','/learning/practice?lesson='+lesson,app=self.app)
            with patch.object(mod,'viewer',AsyncMock(return_value=who(role))),patch.object(mod.company,'info',AsyncMock(return_value={'id':1,'modules':['contracts']})),patch.object(mod.store,'zone',AsyncMock(return_value=ZoneInfo('Europe/Moscow'))):
                response=await mod.guard(req,mod.learning_practice)
            self.assertEqual(response.status,302);self.assertEqual(response.headers['Location'],'/#learning')
        self.pool.execute.assert_not_awaited()

    async def test_real_handler_rechecks_role_before_firm_or_db_access(self):
        mod = self.production
        req = make_mocked_request('GET','/api/learning/context',app=self.app)
        with patch.object(mod,'viewer',AsyncMock(return_value=who('client'))), \
             patch.object(mod.company,'info',AsyncMock()) as info:
            with self.assertRaises(web.HTTPForbidden): await mod.api_learning_context(req)
        info.assert_not_awaited()

    async def test_old_workspace_redirects_without_classroom_bootstrap(self):
        mod=self.production
        for role in ('manager','director'):
            req=make_mocked_request('GET','/learning/workspace',app=self.app)
            with patch.object(mod,'viewer',AsyncMock(return_value=who(role))),patch.object(mod.company,'info',AsyncMock(return_value={'id':1,'modules':[]})),patch.object(mod.store,'zone',AsyncMock(return_value=ZoneInfo('Europe/Moscow'))):
                response=await mod.guard(req,mod.learning_workspace)
            self.assertEqual(response.status,302);self.assertEqual(response.headers['Location'],'/#learning')
            self.assertNotIn('learning-workspace-context',response.text)
        self.pool.execute.assert_not_awaited()

    async def test_workspace_entry_cookie_reset_and_client_denied(self):
        mod = self.production
        req = make_mocked_request('GET', '/learning/enter', app=self.app)
        data = learning.context(who(), {'id': 1, 'modules': []}, 'Europe/Moscow')
        with patch.object(mod, 'learning_context', AsyncMock(return_value=data)):
            first = await mod.learning_enter(req)
            second = await mod.learning_enter(req)
        self.assertEqual(first.headers['Location'],'/#learning');self.assertEqual(second.headers['Location'],'/#learning')
        self.assertNotIn(learning.ENTRY_COOKIE,first.cookies)
        with patch.object(mod, 'viewer', AsyncMock(return_value=who('client'))), \
             patch.object(mod.company, 'info', AsyncMock()) as info:
            with self.assertRaises(web.HTTPForbidden): await mod.learning_workspace(req)
        info.assert_not_awaited()

    async def test_production_routes_only_add_get_learning_endpoints(self):
        app = self.production.build(self.pool)
        learning_routes = [route for route in app.router.routes() if 'learning' in route.resource.canonical]
        self.assertTrue(learning_routes)
        self.assertTrue(all(route.method in {'GET','HEAD'} or (route.method=='POST' and route.resource.canonical=='/api/learning/mode') for route in learning_routes))
        self.pool.execute.assert_not_awaited()

class ExistingSurfaceTests(unittest.TestCase):
    def test_existing_forms_helper_and_gpt_are_unchanged_except_inert_targets(self):
        import re,subprocess
        base='2a11936af00465fafdaa07059da179ee8ce7313a'
        guide_release='2abe9f833e76f80fbf8dc584a32d8d38ef2d29af'
        for name in ['crm/static/helper.js','crm/gpt.py','crm/static/people.js','crm/static/summary.js']:
            process=subprocess.run(['git','show',base+':'+name],cwd=ROOT,capture_output=True,text=True)
            if process.returncode:
                self.skipTest('Pinned Git baseline unavailable for source regression check')
            if name in {'crm/gpt.py', 'crm/static/helper.js', 'crm/static/people.js'}:
                # Verify the guide release separately from later CRM access and workspace changes.
                current=subprocess.check_output(['git','show',guide_release+':'+name],cwd=ROOT,text=True)
            else:
                current=(ROOT/name).read_text()
            current=re.sub(r' data-learning-target="[^"]+"','',current)
            if name.endswith('/review.js'):
                current=current.replace("      if(box.dataset.learningLoaded)document.dispatchEvent(new CustomEvent('crm-review-closed',{detail:{id}}));\n",'').replace("    box.dataset.learningLoaded='true';\n",'').replace("    if (!box.isConnected) return;\n",'')
            self.assertEqual(current,process.stdout,name+' changed functional code')

    def test_learning_does_not_run_automatically_or_call_gpt(self):
        script=(ROOT/'crm/static/learning/learning.js').read_text()
        self.assertNotIn('gptAsk(',script)
        self.assertNotIn('gptTour(',script)
        self.assertNotIn('.click()',script)
        self.assertNotIn('setInterval(',script)
        self.assertIn('window.tourFind ? tourFind(',script)
        self.assertIn("'Escape'",script)
        css=(ROOT/'crm/static/learning/learning.css').read_text()
        self.assertIn('.learn-guide{position:fixed',css)
        self.assertIn('document.body.appendChild(panel)',script)
        self.assertIn('@media(max-width:600px)',css)
        self.assertIn(':focus-visible',css)
        self.assertIn('min-height:44px',css)


if __name__ == "__main__":
    unittest.main()
