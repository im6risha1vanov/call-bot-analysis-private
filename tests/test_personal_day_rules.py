import asyncio
import ipaddress
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
import asyncpg
from crm import personal_day_rules as rules

DSN = os.getenv('DAY_RULES_TEST_DSN')

def check_dsn(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('postgres','postgresql') or parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed.path != '/callbot_methodology_test_day_rules' or parsed.query or parsed.fragment:
        raise RuntimeError('Unsafe test DSN')

@unittest.skipUnless(DSN, 'set DAY_RULES_TEST_DSN to isolated local database')
class RulesIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        check_dsn(DSN); self.pool = await asyncpg.create_pool(DSN,min_size=1,max_size=5)
        async with self.pool.acquire() as c:
            address = await c.fetchval('SELECT inet_server_addr()::text')
            if not address or not ipaddress.ip_interface(address).ip.is_loopback or await c.fetchval('SELECT current_database()') != 'callbot_methodology_test_day_rules': raise RuntimeError('Unsafe database')
            await c.execute('''DROP SCHEMA public CASCADE; CREATE SCHEMA public;
                CREATE TABLE clients(id int PRIMARY KEY); INSERT INTO clients VALUES(1),(2);
                CREATE TABLE crm_personal_profiles(id bigint PRIMARY KEY,telegram_user_id bigint,timezone text);
                INSERT INTO crm_personal_profiles VALUES(1,101,'Europe/Moscow'),(2,102,'UTC');
                CREATE TABLE employees(client_id int,telegram_user_id bigint,role text,archived_at timestamptz);
                INSERT INTO employees VALUES(1,101,'manager',NULL),(1,102,'manager',NULL);
                CREATE TABLE crm_invites(id int,client_id int,telegram_user_id bigint,revoked_at timestamptz);
                INSERT INTO crm_invites VALUES(1,1,101,NULL),(2,1,102,NULL);
                CREATE TABLE crm_personal_tasks(id bigint,owner_id bigint,title text,status text,due_at timestamptz,planned_day date,blocker text);
                INSERT INTO crm_personal_tasks VALUES(1,1,'Mine','planned','2027-01-02T10:00:00Z',NULL,''),(2,2,'Private','planned',NULL,NULL,'');
                CREATE TABLE crm_team_assignments(id bigint,company_id int,assignee_id bigint,title text,status text,due_at timestamptz,blocker text);
                INSERT INTO crm_team_assignments VALUES(3,1,101,'Team','assigned',NULL,'stuck'),(4,2,101,'Other','assigned',NULL,'');''')
            await c.execute(Path('migrations/059_crm_personal_day.sql').read_text(encoding='utf-8'))
            await c.execute(Path('migrations/062_crm_personal_day_rules.sql').read_text(encoding='utf-8'))
            await c.execute("INSERT INTO crm_personal_day_settings(owner_id,morning_time,evening_time) VALUES(1,'09:00','18:00'),(2,'09:00',NULL)")
        self.env = patch.dict(os.environ,{'CRM_PERSONAL_PILOT_TELEGRAM_IDS':'101','CRM_PERSONAL_DAY_RULES_ENABLED':'1'}); self.env.start()
    async def asyncTearDown(self): self.env.stop(); await self.pool.close()
    async def test_concurrent_tick_dedupe_snapshot_and_no_catchup(self):
        now = datetime(2027,1,2,6,0,20,tzinfo=timezone.utc)
        first, second = await asyncio.gather(rules.tick(self.pool,now=now),rules.tick(self.pool,now=now))
        self.assertEqual(len(first)+len(second),1)
        row = await self.pool.fetchrow('SELECT * FROM crm_personal_day_rule_runs')
        self.assertEqual((row['company_id'],row['telegram_user_id'],row['kind'],row['status']),(1,101,'morning','recorded'))
        snap = json.loads(row['snapshot']) if isinstance(row['snapshot'],str) else row['snapshot']
        self.assertEqual([x['title'] for x in snap['tasks']['today']],['Mine'])
        self.assertEqual([x['title'] for x in snap['tasks']['blocked']],['Team'])
        self.assertEqual(await rules.tick(self.pool,now=datetime(2027,1,3,6,1,tzinfo=timezone.utc)),[])
        self.assertEqual(len(await rules.tick(self.pool,now=datetime(2027,1,2,15,0,tzinfo=timezone.utc))),1)
    async def test_dormant_revoked_and_ambiguous(self):
        now = datetime(2027,1,2,6,0,tzinfo=timezone.utc)
        os.environ.pop('CRM_PERSONAL_DAY_RULES_ENABLED'); self.assertEqual(await rules.tick(self.pool,now=now),[])
        os.environ['CRM_PERSONAL_DAY_RULES_ENABLED']='1'
        await self.pool.execute('UPDATE crm_invites SET revoked_at=now() WHERE id=1')
        self.assertEqual(await rules.tick(self.pool,now=now),[])
        await self.pool.execute('UPDATE crm_invites SET revoked_at=NULL WHERE id=1')
        await self.pool.execute("INSERT INTO employees VALUES(2,101,'manager',NULL); INSERT INTO crm_invites VALUES(3,2,101,NULL)")
        self.assertEqual(await rules.tick(self.pool,now=now),[])
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_day_rule_runs'),0)
    async def test_active_employee_pilot_and_null_off(self):
        now = datetime(2027,1,2,6,0,tzinfo=timezone.utc)
        await self.pool.execute('UPDATE employees SET archived_at=now() WHERE telegram_user_id=101')
        self.assertEqual(await rules.tick(self.pool,now=now),[])
        await self.pool.execute('UPDATE employees SET archived_at=NULL WHERE telegram_user_id=101')
        os.environ['CRM_PERSONAL_PILOT_TELEGRAM_IDS']='102'
        self.assertEqual(await rules.tick(self.pool,now=now),[])
        os.environ['CRM_PERSONAL_PILOT_TELEGRAM_IDS']='101'
        await self.pool.execute('UPDATE crm_personal_day_settings SET morning_time=NULL WHERE owner_id=1')
        self.assertEqual(await rules.tick(self.pool,now=now),[])
