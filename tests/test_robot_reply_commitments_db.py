import os, json, uuid, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

DSN=os.getenv('METHODOLOGY_TEST_DATABASE_URL')

@unittest.skipUnless(DSN, 'Requires an isolated PostgreSQL database')
class CommitmentDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        self.admin=await asyncpg.connect(DSN)
        self.schema='commit_test_'+uuid.uuid4().hex
        await self.admin.execute('CREATE SCHEMA '+self.schema)
        self.pool=await asyncpg.create_pool(DSN,server_settings={'search_path':self.schema})
        await self.pool.execute('''CREATE TABLE robot_reply_projects(id BIGINT PRIMARY KEY,owner TEXT);
          CREATE TABLE robot_campaigns(id BIGINT PRIMARY KEY,owner TEXT,timezone TEXT);
          CREATE TABLE robot_reply_project_campaigns(project_id BIGINT,campaign_id BIGINT);
          CREATE TABLE robot_calls(call_id TEXT PRIMARY KEY,owner TEXT,campaign_id BIGINT,phone TEXT,at TIMESTAMPTZ,outcome TEXT,data JSONB);
          CREATE TABLE robot_stoplist(owner TEXT,phone TEXT);
          CREATE TABLE robot_reply_contact_memory(project_id BIGINT,phone TEXT,callback_at TIMESTAMPTZ,verified_by TEXT,updated_at TIMESTAMPTZ DEFAULT now(),PRIMARY KEY(project_id,phone));
          INSERT INTO robot_reply_projects VALUES(1,'owner-a'),(2,'owner-b');
          INSERT INTO robot_campaigns VALUES(11,'owner-a','Europe/Moscow'),(22,'owner-b','Europe/Moscow');
          INSERT INTO robot_reply_project_campaigns VALUES(1,11),(2,22);''')
        await self.pool.execute(Path('migrations/053_robot_reply_commitments.sql').read_text(encoding='utf-8-sig'))

    async def asyncTearDown(self):
        await self.pool.close()
        await self.admin.execute('DROP SCHEMA '+self.schema+' CASCADE')
        await self.admin.close()

    async def test_newer_refusal_and_project_isolation(self):
        from crm.robot_reply_commitments import backfill
        at=datetime(2026,11,16,9,tzinfo=timezone.utc)
        agreement=json.dumps({'transcript':[['robot','Перезвонить завтра в 15:00?'],['client','Да']]},ensure_ascii=False)
        refusal=json.dumps({'transcript':[['client','Больше не звоните']]},ensure_ascii=False)
        await self.pool.execute("INSERT INTO robot_calls VALUES('old','owner-a',11,'79990000000',$1,'callback',$2::jsonb),('new','owner-a',11,'79990000000',$3,'do_not_call',$4::jsonb),('other','owner-b',22,'79990000000',$1,'callback',$2::jsonb)", at,agreement,at+timedelta(hours=1),refusal)
        await backfill(self.pool)
        rows=await self.pool.fetch('SELECT project_id,state,callback_at FROM robot_reply_auto_commitments ORDER BY project_id')
        self.assertEqual([(r['project_id'],r['state']) for r in rows],[(1,'withdrawn'),(2,'agreed')])
        self.assertIsNone(rows[0]['callback_at'])
        await self.pool.execute("UPDATE robot_reply_commitment_scans SET scanned_at=now() WHERE call_id='new'")
        await backfill(self.pool,limit=1)
        self.assertEqual(await self.pool.fetchval('SELECT state FROM robot_reply_auto_commitments WHERE project_id=1'),'withdrawn')
        await self.pool.execute("UPDATE robot_calls SET data='{}'::jsonb WHERE call_id='other'")
        await self.pool.execute("UPDATE robot_reply_commitment_scans SET scanned_at=now()-interval '1 day' WHERE call_id='other'")
        await backfill(self.pool,limit=1)
        self.assertEqual(await self.pool.fetchval('SELECT state FROM robot_reply_auto_commitments WHERE project_id=2'),'withdrawn')

    async def test_e2e_memory_respects_manual_refusal_and_stoplist(self):
        from crm.robot_reply_commitments import backfill
        from crm.robot_reply_library import contact_memory
        now=datetime.now(timezone.utc)
        agreement=json.dumps({'transcript':[['robot','Перезвонить завтра в 15:00?'],['client','Да']]},ensure_ascii=False)
        await self.pool.execute("INSERT INTO robot_calls VALUES('source','owner-a',11,'79990000000',$1,'callback',$2::jsonb)",now,agreement)
        await backfill(self.pool)
        self.assertIn('callback_at',await contact_memory(self.pool,'owner-a',11,'79990000000'))
        self.assertEqual(await contact_memory(self.pool,'owner-b',11,'79990000000'),{})
        manual=now+timedelta(days=2)
        await self.pool.execute("INSERT INTO robot_reply_contact_memory VALUES(1,'79990000000',$1,'director',$2)",manual,now)
        self.assertEqual((await contact_memory(self.pool,'owner-a',11,'79990000000'))['callback_at'],manual.isoformat())
        await self.pool.execute("INSERT INTO robot_calls VALUES('refusal','owner-a',11,'79990000000',$1,'do_not_call','{}'::jsonb)",now+timedelta(seconds=1))
        await backfill(self.pool)
        self.assertEqual(await contact_memory(self.pool,'owner-a',11,'79990000000'),{})
        await self.pool.execute("INSERT INTO robot_stoplist VALUES('owner-a','79990000000')")
        self.assertEqual(await contact_memory(self.pool,'owner-a',11,'79990000000'),{})
