"""Disposable production-schema integration test; no dialing or provider calls.

The DSN must name an empty, disposable database matching
averon_feedback_e2e_YYYYMMDD_HHMMSS. The test writes only synthetic rows and
removes its own rows after the test.
"""
import asyncio
import os
import re
import unittest
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit
from unittest.mock import patch

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL', '')
SAFE_DB = re.compile(r'averon_feedback_e2e_[0-9]{8}_[0-9]{6}\Z')


def _dsn_database(dsn):
    parsed = urlsplit(dsn)
    if parsed.scheme not in ('postgres', 'postgresql'):
        raise ValueError('METHODOLOGY_TEST_DATABASE_URL должен быть PostgreSQL URL')
    name = unquote(parsed.path.lstrip('/'))
    if not SAFE_DB.fullmatch(name):
        raise ValueError('Разрешена только одноразовая БД averon_feedback_e2e_YYYYMMDD_HHMMSS')
    return name


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class FeedbackFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        expected_db = _dsn_database(DSN)  # Validate before connecting.
        import asyncpg
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
        self.addAsyncCleanup(self.pool.close)
        actual_db = await self.pool.fetchval('SELECT current_database()')
        if actual_db != expected_db or not SAFE_DB.fullmatch(actual_db):
            self.fail('Имя подключённой БД не совпадает с разрешённой одноразовой БД')
        for table in ('robot_campaigns','robot_calls','robot_reply_projects','robot_reply_candidates','robot_reply_learning_sources'):
            if await self.pool.fetchval('SELECT EXISTS(SELECT 1 FROM ' + table + ' LIMIT 1)'):
                self.fail('Для теста требуется пустая тестовая БД со схемой production')
        from crm import robot_reply_library as lib
        self.owner = 'feedback-test-' + uuid.uuid4().hex
        self.operator = 'feedback-test-op'
        self.ownership_patch = patch.object(lib.robot_store, '_owned', return_value=None)
        self.ownership_patch.start()
        self.addCleanup(self.ownership_patch.stop)
        self.addAsyncCleanup(self._delete_synthetic_rows)

    async def _delete_synthetic_rows(self):
        # Only delete records under this test's random owner in the already-verified
        # empty disposable database. No table-wide TRUNCATE or production resources.
        owner = self.owner
        await self.pool.execute('DELETE FROM robot_reply_learning_links WHERE owner=$1', owner)
        await self.pool.execute('DELETE FROM robot_reply_learning_clusters WHERE owner=$1', owner)
        await self.pool.execute('DELETE FROM robot_reply_audit WHERE candidate_id IN (SELECT id FROM robot_reply_candidates WHERE owner=$1)', owner)
        await self.pool.execute('DELETE FROM robot_reply_versions WHERE candidate_id IN (SELECT id FROM robot_reply_candidates WHERE owner=$1)', owner)
        await self.pool.execute('DELETE FROM robot_reply_candidates WHERE owner=$1', owner)
        await self.pool.execute('DELETE FROM robot_reply_learning_refresh WHERE owner=$1', owner)
        await self.pool.execute('DELETE FROM robot_reply_learning_sources WHERE owner=$1', owner)
        await self.pool.execute('DELETE FROM robot_calls WHERE owner=$1', owner)
        await self.pool.execute('DELETE FROM robot_reply_project_campaigns WHERE project_id IN (SELECT id FROM robot_reply_projects WHERE owner=$1)', owner)
        await self.pool.execute('DELETE FROM robot_campaigns WHERE owner=$1', owner)
        await self.pool.execute('DELETE FROM robot_reply_projects WHERE owner=$1', owner)

    async def test_store_backfill_refresh_and_approved_payload(self):
        from crm import robot_reply_library as lib, robot_reply_learning as learning, robot_runner
        pool, owner, op = self.pool, self.owner, self.operator
        project = (await lib.create_project(pool, owner, 'Тестовый проект', op, 'generic'))['id']
        other_project = (await lib.create_project(pool, owner, 'Другой проект', op, 'generic'))['id']
        campaign = await pool.fetchval("INSERT INTO robot_campaigns(owner,name,operator,status) VALUES($1,'Тестовая кампания',$2,'draft') RETURNING id",owner,op)
        other_campaign = await pool.fetchval("INSERT INTO robot_campaigns(owner,name,operator,status) VALUES($1,'Другая кампания',$2,'draft') RETURNING id",owner,op)
        await lib.map_campaign(pool,owner,project,campaign)
        await lib.map_campaign(pool,owner,other_project,other_campaign)

        # Fixture audio is local silent PCM. Preview/approval use real application
        # functions; no TTS, HTTP, SIP, scheduling or provider is invoked.
        approved = await lib.create_candidate(pool,owner,project,'Когда можно связаться?','Уточню удобное время.','test-director')
        await pool.execute('UPDATE robot_reply_versions SET audio_pcm=$3,sample_rate=8000,voice_signature=$4 WHERE candidate_id=$1 AND version=$2',
                           approved['id'],approved['version'],b'\x00\x00'*160,'a'*64)
        await lib.preview(pool,owner,approved['id'],approved['version'],'test-director')
        await lib.decide(pool,owner,approved['id'],approved['version'],'test-director','approve')
        original_payload = await lib.approved_payload(pool,owner,campaign,op)
        self.assertEqual(len(original_payload),1)
        self.assertEqual(original_payload[0]['id'],approved['id'])
        self.assertEqual(original_payload[0]['version_id'],approved['version'])

        question = 'Как отправить чертеж?'
        transcript = [['robot','Здравствуйте'],['client',question],
                      ['robot','Как вам удобнее отправить чертеж?'],['client','Через сайт удобно']]
        first_id, foreign_id, second_id = [str(uuid.uuid4()) for _ in range(3)]

        async def save(call_id, camp, phone):
            await robot_runner.store_call(pool,{'call_id':call_id,'owner':owner,'campaign_id':camp,'operator':op,
                'phone':phone,'at':datetime.now(timezone.utc).isoformat(),'mode':'телефон','outcome':'callback',
                'duration_s':30,'transcript':transcript})

        async def cycle():
            await learning.backfill(pool,owner=owner)
            await learning.refresh_all(pool)

        async def discovered():
            return await pool.fetch('''SELECT c.id,c.current_version,c.active_version,c.status,v.question,v.answer,v.approved_at
                FROM robot_reply_candidates c JOIN robot_reply_learning_clusters k ON k.candidate_id=c.id
                JOIN robot_reply_versions v ON v.candidate_id=c.id AND v.version=c.current_version
                WHERE c.owner=$1 AND c.project_id=$2 AND k.intent LIKE 'discovery:%' ''',owner,project)

        await save(first_id,campaign,'79990000001')
        await cycle()
        self.assertEqual(await discovered(),[])
        self.assertEqual(await lib.approved_payload(pool,owner,campaign,op),original_payload)

        # Same utterance in a different campaign/project is not a second source.
        await save(foreign_id,other_campaign,'79990000002')
        await cycle()
        self.assertEqual(await discovered(),[])
        self.assertEqual(await pool.fetchval("SELECT count(*) FROM robot_reply_learning_sources WHERE owner=$1 AND project_id=$2 AND source_type='robot'",owner,project),1)

        await save(second_id,campaign,'79990000003')
        await cycle()
        found = await discovered()
        self.assertEqual(len(found),1)
        draft = found[0]
        self.assertEqual(draft['status'],'draft')
        self.assertIsNone(draft['active_version'])
        self.assertIsNone(draft['approved_at'])
        self.assertEqual(draft['question'],question)
        self.assertEqual(draft['answer'],'Как вам удобнее отправить чертеж?')
        links = await pool.fetch('SELECT source_type,source_id FROM robot_reply_learning_links WHERE owner=$1 AND project_id=$2 AND candidate_id=$3',owner,project,draft['id'])
        self.assertEqual({(r['source_type'],r['source_id']) for r in links},{('robot',first_id),('robot',second_id)})
        self.assertEqual(await lib.approved_payload(pool,owner,campaign,op),original_payload)

        # Replay the same call and worker cycle; ids, versions and active audio stay stable.
        await save(second_id,campaign,'79990000003')
        await cycle()
        again=await discovered()
        self.assertEqual(len(again),1)
        self.assertEqual((again[0]['id'],again[0]['current_version']),(draft['id'],draft['current_version']))
        self.assertEqual(await pool.fetchval("SELECT count(*) FROM robot_calls WHERE owner=$1",owner),3)
        self.assertEqual(await lib.approved_payload(pool,owner,campaign,op),original_payload)
        approved_row=await pool.fetchrow('SELECT current_version,active_version,status FROM robot_reply_candidates WHERE id=$1',approved['id'])
        self.assertEqual((approved_row['current_version'],approved_row['active_version'],approved_row['status']),(1,1,'approved'))


if __name__ == '__main__':
    unittest.main()
