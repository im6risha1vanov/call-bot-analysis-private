import os, unittest, uuid, json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from tests.test_robot_reply_archive import SPEECH

DSN=os.getenv('METHODOLOGY_TEST_DATABASE_URL')

@unittest.skipUnless(DSN,'Requires isolated PostgreSQL')
class ArchiveDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        self.admin=await asyncpg.connect(DSN)
        self.schema='archive_test_'+uuid.uuid4().hex
        await self.admin.execute('CREATE SCHEMA '+self.schema)
        self.pool=await asyncpg.create_pool(DSN,server_settings={'search_path':self.schema})
        await self.pool.execute('''CREATE TABLE robot_reply_projects(id BIGINT,owner TEXT,profile TEXT);
          CREATE TABLE robot_reply_learning_scripts(owner TEXT,project_id BIGINT,client_id INTEGER,script TEXT);
          CREATE TABLE calls(id BIGINT PRIMARY KEY,client_id INTEGER,extension TEXT,direction INTEGER,duration_seconds INTEGER,call_started_at TIMESTAMPTZ,transcript TEXT);
          CREATE TABLE astra_analysis(call_id BIGINT PRIMARY KEY,transcript TEXT);
          CREATE TABLE callcenter_call_sources(client_id INTEGER,call_id BIGINT,tab TEXT,PRIMARY KEY(client_id,call_id));
          CREATE TABLE robot_reply_learning_sources(owner TEXT,project_id BIGINT,source_type TEXT,source_id TEXT,transcript_hash TEXT,status TEXT,reason TEXT,outcome TEXT,metadata JSONB,evidence JSONB,checked_at TIMESTAMPTZ,updated_at TIMESTAMPTZ DEFAULT now(),PRIMARY KEY(owner,project_id,source_type,source_id));
          INSERT INTO robot_reply_projects VALUES(1,'','packaging');
          INSERT INTO robot_reply_learning_scripts VALUES('',1,1,'packaging');''')
        now=datetime(2026,10,1,tzinfo=timezone.utc)
        await self.pool.execute("""INSERT INTO calls VALUES
          (1,1,'14',2,30,$1,$2),(2,1,'15',2,30,$1,''),(3,1,'14',2,30,$3,$2),
          (4,1,'14',2,30,$1,$2),(5,2,'14',2,30,$1,$2),(6,1,'14',1,30,$1,$2)""",now,SPEECH,now+timedelta(days=1))
        await self.pool.execute('INSERT INTO astra_analysis VALUES(2,$1)',SPEECH)
        await self.pool.execute("""INSERT INTO callcenter_call_sources VALUES(1,1,'Картонная упаковка'),(1,2,'Картонная упаковка'),(1,4,'Другой проект');
          INSERT INTO robot_reply_learning_sources VALUES('',1,'human','1','sentinel','extracted','','','{"review":"keep"}','[]',now(),now())""")
        self.specs=[{'id':'olga','configured':True,'extension':'14'}, {'id':'oksana','configured':True,'extension':'15'}, {'id':'robot','configured':True,'extension':'13'}]

    async def asyncTearDown(self):
        await self.pool.close()
        await self.admin.execute('DROP SCHEMA '+self.schema+' CASCADE')
        await self.admin.close()

    async def test_dry_run_apply_idempotence_and_reviewed_preservation(self):
        from crm import robot_reply_archive as arc
        with patch.object(arc.call_analysis,'operators',AsyncMock(return_value=self.specs)):
            preview=await arc.run(self.pool,'',1,1,'14')
            self.assertEqual(preview['written'],0)
            self.assertEqual(preview['counts']['eligible'],2)
            self.assertEqual(preview['counts']['already_imported'],1)
            self.assertEqual(preview['counts']['other_verified_sheet'],1)
            self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM robot_reply_learning_sources'),1)
            self.assertEqual(preview['before_by_operator']['olga']['calls'],1)
            self.assertEqual(preview['after_by_operator']['olga']['calls'],2)
            self.assertGreater(preview['new_by_operator']['oksana']['new_pairs'],0)
            applied=await arc.run(self.pool,'',1,1,'14',apply=True)
            self.assertEqual(applied['written'],2)
            self.assertFalse(applied['after_is_projection'])
            self.assertEqual(applied['after_by_operator']['olga']['calls'],2)
            self.assertEqual(applied['after_by_operator']['oksana']['calls'],1)
            again=await arc.run(self.pool,'',1,1,'14',apply=True)
            self.assertEqual(again['written'],0)
            row=await self.pool.fetchrow("SELECT transcript_hash,metadata FROM robot_reply_learning_sources WHERE source_id='1'")
            self.assertEqual(row['transcript_hash'],'sentinel')
            self.assertEqual(json.loads(row['metadata'])['review'],'keep')
            with self.assertRaises(ValueError): await arc.run(self.pool,'other',1,1,'14')
