"""Run only on the explicitly named disposable PostgreSQL validation database.

REPLY_TEST_DSN=postgresql:///averon_reply_validation_20261118 python -m pytest tests/test_robot_reply_pg.py
Apply migrations/051 and 052 via normal release migration procedure first.
"""
import asyncio
import os
import uuid
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

DSN = os.getenv('REPLY_TEST_DSN', '')
pytestmark = pytest.mark.skipif('averon_reply_validation_20261118' not in DSN, reason='explicit disposable validation DB required')


def test_postgres_lifecycle(monkeypatch):
    import asyncpg
    from crm import robot_reply_library as lib
    from crm import robot_reply_learning as learning
    from voice import operator, speech
    op = SimpleNamespace(voice='alena', voice_role='', voice_speed=1.0)
    monkeypatch.setattr(operator, 'load', lambda *_: op)
    monkeypatch.setattr(lib.robot_store, '_owned', lambda *_: None)
    import httpx
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'audio_pcm_b64':base64.b64encode(b'\x11\x22' * 2000).decode(), 'sample_rate':8000, 'voice_signature':lib.voice_signature(op)}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): return Response()
    monkeypatch.setattr(httpx, 'AsyncClient', Client)

    async def run():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        try:
            assert await pool.fetchval('SELECT current_database()') == 'averon_reply_validation_20261118'
            owner = 'test-' + uuid.uuid4().hex
            project = await lib.create_project(pool, owner, 'Тест', 'testop')
            pid = project['id']
            candidate = await lib.create_candidate(pool, owner, pid, 'Сколько стоит?', 'Назовите размер.', 'director')
            cid = candidate['id']
            unverified = await lib.create_candidate(pool, owner, pid, 'Где вы?', 'Адрес уточняется.', 'director')
            await pool.execute('UPDATE robot_reply_versions SET fact_needed=true,fact_note=$2 WHERE candidate_id=$1', unverified['id'], 'Адрес неизвестен')
            with pytest.raises(ValueError, match='неподтверждённые'):
                await lib.generate(pool, owner, unverified['id'], 1, 'director')
            with pytest.raises(ValueError, match='неподтверждённые'):
                await lib.decide(pool, owner, unverified['id'], 1, 'director', 'approve')
            revised = await lib.edit(pool, owner, unverified['id'], 1, 'Где вы?', 'Адрес уточнит менеджер.', 'director', fact_checked=True)
            assert revised['version'] == 2
            assert await lib.items(pool, 'another-owner', pid) == []
            with pytest.raises(ValueError):
                await lib.decide(pool, owner, cid, 1, 'director', 'approve')
            await lib.generate(pool, owner, cid, 1, 'director')
            wav = await lib.audio(pool, owner, cid, 1, 'director')
            assert wav[:4] == b'RIFF' and b'\x11\x22' in wav
            with pytest.raises(ValueError):
                await lib.decide(pool, owner, cid, 1, 'director', 'approve')
            await lib.preview(pool, owner, cid, 1, 'director')
            await lib.decide(pool, owner, cid, 1, 'director', 'approve')
            campaign = await pool.fetchval("INSERT INTO robot_campaigns(owner,name,operator) VALUES($1,'Тест','testop') RETURNING id", owner)
            await lib.map_campaign(pool, owner, pid, campaign)
            payload = await lib.approved_payload(pool, owner, campaign, 'testop')
            assert len(payload) == 1 and payload[0]['version_id'] == 1 and payload[0]['sample_rate'] == 8000
            assert await lib.approved_payload(pool, 'another-owner', campaign, 'testop') == []
            await lib.save_contact_memory(pool, owner, pid, '79991234567', (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), 'director')
            assert 'callback_at' in await lib.contact_memory(pool, owner, campaign, '79991234567')
            assert await lib.contact_memory(pool, owner, campaign, '79991234568') == {}
            assert await lib.contact_memory(pool, 'another-owner', campaign, '79991234567') == {}
            await pool.execute('''INSERT INTO robot_reply_auto_commitments(project_id,phone,state,callback_at,source_call_id,source_at)
              VALUES($1,'79991234568','agreed',now()+interval '1 day','test-call',now())''',pid)
            assert 'callback_at' in await lib.contact_memory(pool, owner, campaign, '79991234568')
            await pool.execute('''UPDATE robot_reply_auto_commitments SET state='withdrawn',callback_at=NULL,
              source_at=now()+interval '1 second' WHERE project_id=$1 AND phone='79991234568' ''',pid)
            assert await lib.contact_memory(pool, owner, campaign, '79991234568') == {}
            await pool.execute("INSERT INTO robot_stoplist(owner,phone,reason) VALUES($1,'79991234567','test')",owner)
            assert await lib.contact_memory(pool, owner, campaign, '79991234567') == {}
            with pytest.raises(ValueError):
                await lib.decide(pool, owner, cid, 1, 'director', 'reject', 'no')
            await lib.edit(pool, owner, cid, 1, 'Сколько?', 'Уточню.', 'director', ['печать'], [])
            with pytest.raises(ValueError):
                await lib.edit(pool, owner, cid, 1, 'stale', 'stale', 'director')
            await lib.decide(pool, owner, cid, 2, 'director', 'reject', 'Проверить')
            result = await lib.rollback(pool, owner, cid, 2, 1, 'director')
            assert result['active_version'] == 1
            await learning.ingest_source(pool, owner, pid, 'robot', 'one', [['client','Сколько стоит?'],['robot','Назовите размер.']])
            await learning.ingest_source(pool, owner, pid, 'robot', 'one', [['client','Сколько стоит?'],['robot','Назовите размер.']])
            coverage = await learning.coverage(pool, owner, pid)
            assert sum(x['count'] for x in coverage) == 1
            assert await learning.coverage(pool, 'wrong', pid) == []
        finally:
            await pool.close()
    asyncio.run(run())


def test_postgres_auto_audio_queue(monkeypatch):
    import asyncpg
    import httpx
    from crm import robot_reply_library as lib
    calls = []
    failed = {'on': False}
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'audio_pcm_b64':base64.b64encode(b'\x11\x22' * 2000).decode(), 'sample_rate':8000, 'voice_signature':'a'*64}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            calls.append(kwargs['json']['text'])
            await asyncio.sleep(.05)
            if failed['on']: raise httpx.ConnectError('provider unavailable')
            return Response()
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    monkeypatch.setattr(lib.robot_store, '_owned', lambda *_: None)

    async def run():
        # A single-slot pool catches accidental nested acquisition deadlocks.
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=1)
        try:
            assert await pool.fetchval('SELECT current_database()') == 'averon_reply_validation_20261118'
            owner = 'audio-test-' + uuid.uuid4().hex
            project = await lib.create_project(pool, owner, 'Авто', 'testop')
            async def draft(answer, kind='learned', fact=False):
                item = await lib.create_candidate(pool,owner,project['id'],'Вопрос клиента?',answer,'director')
                await pool.execute('UPDATE robot_reply_candidates SET source_kind=$2 WHERE id=$1',item['id'],kind)
                await pool.execute('UPDATE robot_reply_versions SET fact_needed=$2 WHERE candidate_id=$1',item['id'],fact)
                if kind != 'manual':
                    await pool.execute("INSERT INTO robot_reply_audit(candidate_id,version,actor,action) VALUES($1,1,'learning','draft')",item['id'])
                return item['id']
            eligible = await draft('Подскажите нужный размер?')
            fact = await draft('Уточним сведения.',fact=True)
            manual = await draft('Ручной ответ.',kind='manual')
            digits = await draft('Цена будет стоить 100 рублей.')
            results = await asyncio.wait_for(asyncio.gather(lib.prepare_pending_audio(pool,owner=owner),lib.prepare_pending_audio(pool,owner=owner)), timeout=10)
            assert sum(x['prepared'] for x in results) == 1
            assert len(calls) == 1
            rows = await pool.fetch('SELECT candidate_id,audio_pcm,approved_at FROM robot_reply_versions WHERE candidate_id=ANY($1::bigint[])',[eligible,fact,manual,digits])
            byid = {r['candidate_id']: r for r in rows}
            assert byid[eligible]['audio_pcm'] and not byid[eligible]['approved_at']
            assert all(byid[k]['audio_pcm'] is None for k in (fact,manual,digits))
            failing = await draft('Когда удобно поговорить?')
            failed['on'] = True
            await lib.prepare_pending_audio(pool,owner=owner)
            job=await pool.fetchrow('SELECT attempts,next_retry_at,last_error FROM robot_reply_audio_jobs WHERE candidate_id=$1',failing)
            assert job['attempts']==1 and job['last_error']=='ConnectError'
            count = len(calls)
            await lib.prepare_pending_audio(pool,owner=owner)
            assert len(calls)==count
            await pool.execute("UPDATE robot_reply_audio_jobs SET next_retry_at=now()-interval '1 second' WHERE candidate_id=$1",failing)
            failed['on']=False
            await lib.prepare_pending_audio(pool,owner=owner)
            assert await pool.fetchval('SELECT audio_pcm IS NOT NULL FROM robot_reply_versions WHERE candidate_id=$1',failing)
        finally:
            await pool.close()
    asyncio.run(run())
