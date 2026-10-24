"""Компании не видят друг друга: обход всех адресов CRM от лица второй компании.

В первой компании лежат данные с меткой СЕКРЕТ и номером 79115550001 во всех таблицах. Директор и менеджер
второй компании проходят все адреса API (подставляя номера записей первой компании), и ни один ответ не должен
содержать метку. Запросы на запись с чужими номерами записей не должны менять ни одной строки первой компании.
Новый адрес в web.py попадает в обход сам.
"""
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from crm import auth, company
from tests.test_crm import DSN, TEST_KEY, DBCase

SECRET, PHONE = 'СЕКРЕТ', '79115550001'
ROOT = Path(__file__).resolve().parents[1]

# Пока общие на все компании; разделяются на этапе 3 плана (робот — этап 7). Убирать отсюда по мере разделения.
SHARED_YET = ('/api/robot', '/api/prompts', '/api/docs')

# Не обходим: вход и выход, внешние сервисы (LLM, ElevenLabs, DaData, Манго-звонок), страницы не API.
SKIP = {'/api/login', '/api/logout', '/api/test/login', '/api/ask', '/api/gpt', '/api/robot/preview', '/api/robot/call',
        '/api/robot/voices/clone', '/api/robot/voices/add', '/api/robot/voices', '/api/robot/voices/sample',
        '/api/contracts/lookup/{kind}/{value}'}

# Таблицы с данными компании: снимок строк первой компании до и после чужих запросов на запись.
TABLES = ('calls', 'call_reviews', 'callback_tasks', 'crm_leads', 'crm_outcomes', 'crm_reports', 'crm_questions',
          'crm_invites', 'employees', 'manager_memory', 'presentations', 'deal_events', 'contracts',
          'source_selections', 'training_sessions', 'review_copy_lists')


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class TenantIsolationTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for name in ('020_robot.sql', '029_averongpt.sql', '030_company_settings.sql'):
            await self.pool.execute((ROOT / 'migrations' / name).read_text())
        await self.pool.execute('TRUNCATE robot_contacts, robot_stoplist, robot_campaigns, robot_targets, robot_calls CASCADE')
        from aiohttp.test_utils import TestClient, TestServer
        from crm import web
        web.SECURE_COOKIE = False
        self.http = TestClient(TestServer(web.build(self.pool)))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)
        await self.seed_secret()
        # Вторая компания: тот же добавочный 101, чтобы поймать выборки по добавочному без компании.
        await self.pool.execute("INSERT INTO clients(id,timezone,processing_enabled) VALUES(2,'Europe/Moscow',true)")
        # Все модули включены: закрытый модуль не должен прятать утечку от обхода.
        await self.pool.execute("INSERT INTO company_settings(client_id,name,modules) VALUES(2,'Вторая',$1)", list(company.MODULES))
        await self.pool.execute("INSERT INTO employees VALUES(2,'101',223,'manager','Свой менеджер'),"
                                "(2,'head',902,'head','Свой РОП')")
        await self.pool.execute("INSERT INTO calls(client_id,extension,call_started_at,client_number,duration_seconds) "
                                "VALUES(2,'101',now()-'1 hour'::interval,'79990002222',60)")

    async def seed_secret(self):
        p = self.pool
        self.ids = {}
        call = await p.fetchval(
            "INSERT INTO calls(client_id,extension,transcript,call_started_at,client_number,duration_seconds,recording_id) "
            "VALUES(1,'101',$1,now()-'1 hour'::interval,$2,95,'rec1') RETURNING id", SECRET + ' разговор', PHONE)
        self.ids['call'] = call
        data = {'kind': 'cold_first', 'success': False, 'chance': 'hot', 'client': {'company': SECRET + ' Ромашка'},
                'rop_line': SECRET, 'key_moment': {'category': 'no_close', 'what_happened': SECRET}}
        await p.execute("INSERT INTO call_reviews(call_id,client_id,extension,prompt_version,kind,success,chance,category,data) "
                        "VALUES($1,1,'101','v',$2,false,'hot','no_close',$3::jsonb)", call, data['kind'], json.dumps(data))
        await p.execute("UPDATE employees SET full_name=$1 || ' ' || full_name WHERE client_id=1", SECRET)
        self.ids['task'] = await p.fetchval(
            "INSERT INTO callback_tasks(client_id,extension,call_id,client_number,company,kind,due_date,raw) "
            "VALUES(1,'101',$1,$2,$3,'callback',current_date,$3) RETURNING id", call, PHONE, SECRET)
        self.ids['lead'] = await p.fetchval(
            "INSERT INTO crm_leads(client_id,extension,number,name,company) VALUES(1,'101',$1,$2,$2) RETURNING id", PHONE, SECRET)
        await p.execute("INSERT INTO crm_outcomes(client_id,extension,number,outcome,comment) VALUES(1,'101',$1,'callback',$2)",
                        PHONE, SECRET)
        self.ids['report'] = await p.fetchval(
            "INSERT INTO crm_reports(client_id,kind,title,extension,body) VALUES(1,'daily',$1,'101',$1) RETURNING id", SECRET)
        await p.execute("INSERT INTO crm_questions(client_id,role,extension,question,answer) VALUES(1,'director',NULL,$1,$1),"
                        "(1,'manager','101',$1,$1)", SECRET)
        self.ids['invite'] = await p.fetchval(
            "INSERT INTO crm_invites(token_hash,role,client_id,extension,name,login) VALUES('h-secret','manager',1,'101',$1,'secretlogin') "
            "RETURNING id", SECRET)
        card = {'weak': [{'title': SECRET}], 'focus': SECRET}
        await p.execute("INSERT INTO manager_memory(client_id,extension,day,card,stats,prompt_version) "
                        "VALUES(1,'101',current_date,$1::jsonb,'{}'::jsonb,'v')", json.dumps(card))
        self.ids['presentation'] = await p.fetchval(
            "INSERT INTO presentations(client_id,file_id,day,extension,status,call_id,data,comment) "
            "VALUES(1,'f-secret',current_date,'101','reviewed',$1,$2::jsonb,$3) RETURNING id",
            call, json.dumps({'kind': 'presentation', 'rop_line': SECRET, 'contract': {'company': SECRET}}), SECRET)
        await p.execute("INSERT INTO deal_events(client_id,phone,kind,note,extension) VALUES(1,$1,'note',$2,'101')",
                        PHONE[-10:], SECRET)
        self.ids['contract'] = await p.fetchval(
            "INSERT INTO contracts(client_id,num,type,contract_date,company,inn,amount,phone,extension) "
            "VALUES(1,77,'ops',current_date,$1,'7700000000',1000,$2,'101') RETURNING id", SECRET, PHONE[-10:])
        self.ids['selection'] = await p.fetchval(
            "INSERT INTO source_selections(client_id,niche,niche_name,city,donors,author_ext,author_name) "
            "VALUES(1,'kitchen',$1,$1,$2::jsonb,'101',$1) RETURNING id",
            SECRET, json.dumps([{'name': SECRET, 'phone': PHONE}]))
        self.ids['training'] = await p.fetchval(
            "INSERT INTO training_sessions(client_id,extension,topic,status,transcript) "
            "VALUES(1,'101',$1,'completed',$2::jsonb) RETURNING id", SECRET, json.dumps([{'role': 'user', 'text': SECRET}]))
        await p.execute("INSERT INTO review_copy_lists(client_id,tokens) VALUES(1,ARRAY[$1])", SECRET)

    async def enter(self, who):
        """Сессия второй компании: директор и клиент по учётке, менеджер по Telegram."""
        token = 'tenant2-' + who
        if who in ('director', 'client'):
            await self.pool.execute("INSERT INTO crm_sessions(token_hash,expires_at,role,client_id,client_key,is_test) "
                                    "VALUES($1,now()+'1 day',$2,2,$3,false) ON CONFLICT DO NOTHING", auth.digest(token), who,
                                    'Свой клиент' if who == 'client' else None)
        else:
            await self.pool.execute("INSERT INTO crm_sessions(token_hash,telegram_user_id,expires_at) VALUES($1,223,now()+'1 day') ON CONFLICT DO NOTHING",
                                    auth.digest(token))
        self.http.session.cookie_jar.clear()
        self.http.session.cookie_jar.update_cookies({'crm_session': token})

    def routes(self, method):
        out = []
        for route in self.http.server.app.router.routes():
            info = route.resource.get_info() if route.resource else {}
            path = info.get('path') or info.get('formatter') or ''
            if route.method == method and path.startswith('/api/') and path not in SKIP:
                out.append(path)
        return sorted(set(out))

    def fill(self, path):
        """Подставляет в адрес номера записей первой компании."""
        any_id = {'/api/recording/': 'call', '/api/review/': 'call', '/api/trainings/': 'training',
                  '/api/sources/': 'selection', '/api/presentations/': 'presentation', '/api/access/': 'invite',
                  '/api/tasks/': 'task', '/api/leads/': 'lead'}
        rid = next((self.ids[v] for k, v in any_id.items() if path.startswith(k)), self.ids['call'])
        path = re.sub(r'\{id(:[^}]*)?\}', str(rid), path)
        return (path.replace('{phone}', PHONE[-10:]).replace('{kind}', 'review').replace('{action}', 'delete'))

    QUERY = {'ext': '101', 'number': PHONE, 'phone': PHONE, 'id': '1', 'call': '1', 'client': SECRET, 'person': '101',
             'days': '30', 'period': '30', 'q': '', 'owner': SECRET, 'path': 'pipe/'}

    async def crawl_reads(self, who):
        leaks = []
        for path in self.routes('GET'):
            url = self.fill(path)
            r = await self.http.get(url, params=self.QUERY)
            text = (await r.read()).decode('utf-8', 'replace')
            self.statuses.append((who, 'GET', url, r.status, text[:90]))
            if SECRET in text and not path.startswith(SHARED_YET):
                leaks.append(f'{who} GET {url} → {r.status}: {text[:160]}')
        return leaks

    async def snapshot(self):
        out = {}
        for table in TABLES:
            rows = await self.pool.fetch(f'SELECT to_jsonb(t) AS j FROM {table} t WHERE client_id=1')
            out[table] = sorted(json.dumps(dict(json.loads(r['j'])), sort_keys=True, default=str) for r in rows)
        return out

    async def crawl_writes(self, who):
        before = await self.snapshot()
        h = {'X-CRM': '1'}
        payload = {'id': self.ids['call'], 'phone': PHONE, 'number': PHONE, 'ext': '101', 'extension': '101',
                   'kind': 'note', 'note': 'чужой', 'status': 'done', 'outcome': 'not_interested', 'call_id': self.ids['call'],
                   'phone_client': PHONE, 'reason': 'price', 'client': SECRET, 'tg': '123', 'role': 'manager'}
        for method in ('POST', 'DELETE'):
            for path in self.routes(method):
                if path.startswith(SHARED_YET):
                    continue
                url = self.fill(path)
                r = await self.http.request(method, url, json=payload, headers=h)
                self.statuses.append((who, method, url, r.status, (await r.text())[:90]))
        after = await self.snapshot()
        return [f'{who}: изменилась таблица {t} первой компании' for t in TABLES if before[t] != after[t]]

    async def test_second_company_sees_nothing_of_the_first(self):
        mango = patch('mango_client.fetch_recording', AsyncMock(return_value=(SECRET.encode(), 'audio/mpeg')))
        callback = patch('mango_client.callback', AsyncMock(return_value={'result': 1000}))
        with mango, callback, patch.dict(os.environ, {'CRM_TEST_KEY': TEST_KEY}):
            leaks, self.statuses = [], []
            for who in ('director', 'manager', 'client'):
                await self.enter(who)
                leaks += await self.crawl_reads(who)
            for who in ('director', 'manager', 'client'):
                await self.enter(who)
                leaks += await self.crawl_writes(who)
        if os.getenv('TENANT_DEBUG'):
            print('\n'.join(' '.join(map(str, x)) for x in self.statuses))
        self.assertEqual(leaks, [], '\n'.join(leaks))

    async def test_crawl_reaches_the_api(self):
        """Обход не пустой и видит свои данные: без этого тест выше ничего бы не проверял."""
        self.assertGreater(len(self.routes('GET')), 30)
        self.assertGreater(len(self.routes('POST')), 30)
        await self.enter('director')
        r = await self.http.get('/api/me')
        self.assertEqual(r.status, 200, await r.text())
        r = await self.http.get('/api/journal', params={'period': '7'})
        self.assertIn('9990002222', await r.text())

    async def test_viewer_knows_its_company(self):
        """У клиента компании нет actor: компания берётся из сессии, а не «первая в базе»."""
        from crm import gpt
        await self.enter('client')
        who = await auth.viewer_for(self.pool, 'tenant2-client')
        self.assertEqual((who.role, who.company), ('client', 2))
        self.assertEqual(await gpt.client_id_of(self.pool, who), 2)
        await self.enter('manager')
        self.assertEqual((await auth.viewer_for(self.pool, 'tenant2-manager')).company, 2)

    async def test_bot_sheet_takes_only_our_company(self):
        """Таблица бота (GOOGLE_SHEET_ID) наша: звонки других компаний в неё не попадают."""
        from review import sheets
        with patch.dict(os.environ, {'CRM_HOME_COMPANY': '2'}):
            tables = await sheets.collect(self.pool)
        text = json.dumps([t.rows for t in tables], ensure_ascii=False, default=str)
        self.assertNotIn(SECRET, text)
        with patch.dict(os.environ, {'CRM_HOME_COMPANY': '1'}):
            tables = await sheets.collect(self.pool)
        self.assertIn(SECRET, json.dumps([t.rows for t in tables], ensure_ascii=False, default=str))


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class CompanySettingsTests(DBCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.pool.execute((ROOT / 'migrations' / '030_company_settings.sql').read_text())
        await self.pool.execute('TRUNCATE company_settings')
        await self.pool.execute("INSERT INTO clients(id,timezone,processing_enabled) VALUES(3,'Europe/Moscow',true)")
        from aiohttp.test_utils import TestClient, TestServer
        from crm import web
        web.SECURE_COOKIE = False
        self.http = TestClient(TestServer(web.build(self.pool)))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)

    async def enter(self, client_id):
        token = f'company-{client_id}'
        await self.pool.execute("INSERT INTO crm_sessions(token_hash,expires_at,role,client_id,is_test) "
                                "VALUES($1,now()+'1 day','director',$2,false) ON CONFLICT DO NOTHING", auth.digest(token), client_id)
        self.http.session.cookie_jar.update_cookies({'crm_session': token})

    async def test_home_company_keeps_env_settings(self):
        """Наша компания без строки настроек работает как раньше: все модули, таблица из .env."""
        with patch.dict(os.environ, {'KITCHEN_SHEET_ID': 'leads-sheet', 'CONTRACT_OUR_EMAIL': 'we@example.ru'}):
            info = await company.info(self.pool, 1)
            self.assertEqual((info['home'], info['modules']), (True, list(company.MODULES)))
            self.assertEqual(info['settings'], {'callcenter_sheet': 'leads-sheet', 'contract_email': 'we@example.ru'})
            # другой компании .env не достаётся
            other = await company.info(self.pool, 3)
            self.assertEqual((other['home'], other['modules'], other['settings']), (False, list(company.DEFAULT_MODULES), {}))

    async def test_modules_close_pages_and_api(self):
        await self.enter(3)
        me = await (await self.http.get('/api/me')).json()
        self.assertEqual((me['company'], me['modules']), ('Компания 3', list(company.DEFAULT_MODULES)))
        for path in ('/api/callcenter', '/api/robot', '/api/robot/cab/campaigns', '/api/contracts', '/api/contracts/config'):
            r = await self.http.get(path)
            self.assertEqual(r.status, 403, path)
            self.assertIn('не подключён', (await r.json())['error'])
        self.assertEqual((await self.http.get('/api/sources/selections')).status, 200)
        await self.pool.execute("INSERT INTO company_settings(client_id,name,modules,settings) VALUES(3,'Окна Юг',$1,$2::jsonb)",
                                ['contracts'], json.dumps({'contract_email': 'okna@example.ru'}))
        me = await (await self.http.get('/api/me')).json()
        self.assertEqual((me['company'], me['modules']), ('Окна Юг', ['contracts']))
        cfg = await (await self.http.get('/api/contracts/config')).json()
        self.assertEqual(cfg['ourEmail'], 'okna@example.ru')
        self.assertEqual((await self.http.get('/api/sources/selections')).status, 403)

    async def test_callcenter_sheet_per_company(self):
        """Колл-центр читает таблицу своей компании; без таблицы — понятная ошибка, а не наша таблица."""
        from crm import cabinet
        await self.pool.execute("INSERT INTO company_settings(client_id,modules,settings) VALUES(3,$1,$2::jsonb)",
                                ['callcenter'], json.dumps({'callcenter_sheet': 'their-sheet'}))
        with patch.dict(os.environ, {'KITCHEN_SHEET_ID': 'our-sheet'}):
            self.assertEqual(await cabinet.sheet_of(self.pool, 3), 'their-sheet')
            self.assertEqual(await cabinet.sheet_of(self.pool, 1), 'our-sheet')
            await self.pool.execute("UPDATE company_settings SET settings='{}' WHERE client_id=3")
            with self.assertRaisesRegex(LookupError, 'не подключена'):
                await cabinet.sheet_of(self.pool, 3)
