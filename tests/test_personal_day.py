import os
import unittest
import ipaddress
from urllib.parse import urlsplit
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo
import asyncpg
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from crm import personal_day as day
from tools import Forbidden

DSN = os.getenv('PERSONAL_DAY_TEST_DSN')


def safe_test_dsn(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('postgres', 'postgresql') or parsed.hostname not in ('localhost', '127.0.0.1', '::1') or parsed.path != '/callbot_methodology_test_personal_day' or parsed.query or parsed.fragment:
        raise RuntimeError('Refusing non-loopback or unexpected test database DSN')


class DayRules(unittest.TestCase):
    def test_timezone_and_priority_of_blocker(self):
        zone = ZoneInfo('Europe/Moscow')
        row = {'due_at':datetime(2027,1,1,22,30,tzinfo=timezone.utc),'planned_day':None,'blocker':''}
        self.assertEqual(day.classify(row,date(2027,1,2),zone,'personal'),'today')
        row['blocker'] = 'Waiting'
        self.assertEqual(day.classify(row,date(2027,1,3),zone,'personal'),'blocked')
    def test_dst_bounds_and_dsn_safety(self):
        start, end = day.day_bounds_utc(date(2027,3,28), ZoneInfo('Europe/Berlin'))
        self.assertEqual((end-start).total_seconds(), 23*3600)
        safe_test_dsn('postgresql://u@localhost:55478/callbot_methodology_test_personal_day')
        for dsn in ('postgresql://u@remote/callbot_methodology_test_personal_day', 'postgresql://u@localhost/other', 'postgresql://u@localhost/callbot_methodology_test_personal_day?host=remote'):
            with self.assertRaises(RuntimeError): safe_test_dsn(dsn)
    def test_invalid_hours_and_timezone_free_time(self):
        data={'work_start':'18:00','work_end':'09:00','priorities':[], 'morning_time':None,'evening_time':None,'version':0}
        with self.assertRaises(ValueError): day.settings_data(data)
        data.update(work_start='09:00',work_end='18:00',morning_time='09:00+03:00')
        with self.assertRaises(ValueError): day.settings_data(data)

@unittest.skipUnless(DSN, 'set PERSONAL_DAY_TEST_DSN to isolated database')
class DayIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        safe_test_dsn(DSN)
        self.pool = await asyncpg.create_pool(DSN)
        async with self.pool.acquire() as c:
            addr = await c.fetchval('select inet_server_addr()::text')
            if not addr or not ipaddress.ip_interface(addr).ip.is_loopback or await c.fetchval('select current_database()') != 'callbot_methodology_test_personal_day': raise RuntimeError('Unsafe database')
            await c.execute('''DROP SCHEMA public CASCADE; CREATE SCHEMA public;
              CREATE TABLE crm_personal_profiles(id bigint PRIMARY KEY,telegram_user_id bigint,timezone text);
              INSERT INTO crm_personal_profiles VALUES(1,101,'Europe/Moscow'),(2,102,'UTC');
              CREATE TABLE crm_personal_tasks(id bigint,owner_id bigint,title text,status text,due_at timestamptz,planned_day date,blocker text);
              INSERT INTO crm_personal_tasks VALUES(1,1,'mine','planned','2027-01-01T22:30:00Z',NULL,''),(2,2,'other','planned','2027-01-02T12:00:00Z',NULL,'');
              CREATE TABLE crm_team_assignments(id bigint,company_id int,assignee_id bigint,title text,status text,due_at timestamptz,blocker text,deal_id text);
              INSERT INTO crm_team_assignments VALUES(3,1,101,'blocked','assigned',NULL,'help',NULL),(4,2,101,'other company','assigned',NULL,'help',NULL);''')
            await c.execute(Path('migrations/059_crm_personal_day.sql').read_text(encoding='utf-8'))
        async def fake_identity(db, who): return (1,101)
        self.patch = patch.object(day.team_assignments,'identity',fake_identity); self.patch.start()
        async def viewer(req): return SimpleNamespace()
        async def body(req): return await req.json()
        @web.middleware
        async def errors(req, handler):
            try: return await handler(req)
            except ValueError: raise web.HTTPBadRequest()
            except Forbidden: raise web.HTTPForbidden()
        key=web.AppKey('pool'); app=web.Application(middlewares=[errors]); app[key]=self.pool; app.add_routes(day.routes(viewer,body,key))
        self.client=TestClient(TestServer(app)); await self.client.start_server()
    async def asyncTearDown(self):
        self.patch.stop(); await self.client.close(); await self.pool.close()
    async def test_plan_is_private_and_timezone_aware(self):
        r=await self.client.get('/api/personal/day?date=2027-01-02'); data=await r.json()
        self.assertEqual([x['title'] for x in data['today']],['mine'])
        self.assertEqual([x['title'] for x in data['blocked']],['blocked'])
        self.assertIsNone(data['appointments']); self.assertEqual(data['appointments_status'],'not_connected')
    async def test_settings_version_conflict(self):
        data={'work_start':'09:00','work_end':'18:00','priorities':['A'], 'morning_time':'08:30','evening_time':None,'version':0}
        a=await self.client.put('/api/personal/day/settings',json=data); self.assertEqual(a.status,200)
        b=await self.client.put('/api/personal/day/settings',json=data); self.assertEqual(b.status,409)
        r=await self.client.get('/api/personal/day/settings'); self.assertEqual((await r.json())['settings']['version'],1)
