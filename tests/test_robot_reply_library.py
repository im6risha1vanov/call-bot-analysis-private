import asyncio
from types import SimpleNamespace

import pytest

from crm import robot_reply_library as lib
from tools import Forbidden
from voice import operator, speech


def test_actor_requires_identifiable_director():
    with pytest.raises(Forbidden):
        lib.actor_id(SimpleNamespace(role='client', test=False, invite_id=1, actor=None))
    with pytest.raises(Forbidden):
        lib.actor_id(SimpleNamespace(role='director', test=True, invite_id=1, actor=None))
    assert lib.actor_id(SimpleNamespace(role='director', test=False, invite_id=12, actor=None)) == 'invite:12'


def test_telemetry_does_not_retain_transcript_or_contact():
    from crm.robot_runner import reply_metrics
    result = reply_metrics({'reply_library_events': [{'reply_id': 7, 'version_id': 2, 'outcome': 'completed',
              'response_ms': 23, 'trigger': 'Личный номер и имя', 'phone': '79876543210'}]})
    assert result == [{'reply_id': 7, 'version_id': 2, 'outcome': 'completed', 'response_ms': 23, 'first_audio_ms': None}]


def test_callback_requires_timezone():
    with pytest.raises(ValueError, match='часовым поясом'):
        asyncio.run(lib.save_contact_memory(None, '', 1, '79991234567', '2026-11-19T12:00', 'director'))


def test_learning_does_not_block_dispatch(monkeypatch):
    from crm import robot_runner
    async def run():
        ticked = asyncio.Event()
        async def learning(_):
            await asyncio.Event().wait()
        async def ingest(_):
            pass
        async def tick(_):
            ticked.set()
        monkeypatch.setattr(robot_runner, 'learning_loop', learning)
        monkeypatch.setattr(robot_runner, 'ingest', ingest)
        monkeypatch.setattr(robot_runner, 'tick', tick)
        task = asyncio.create_task(robot_runner.loop(None))
        await asyncio.wait_for(ticked.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())


def test_payload_scoped_and_fail_closed(monkeypatch):
    op = SimpleNamespace(voice='alena', voice_role='', voice_speed=1.0)
    monkeypatch.setattr(lib.robot_store, '_owned', lambda *args: None)
    monkeypatch.setattr(operator, 'load', lambda *args: op)
    row = dict(id=1, version=2, question='Почему?', answer='Потому.', triggers=[], next_action='reply', context_terms=[], contact_terms=[], audio_pcm=b'\x01\x00', sample_rate=speech.TTS_RATE, voice_signature=lib.voice_signature(op))
    class Pool:
        async def fetch(self, sql, owner, campaign_id, operator_id):
            assert (owner,campaign_id,operator_id) == ('client', 4, 'op')
            assert 'v.approved_at IS NOT NULL' in sql
            assert 'campaign.owner=p.owner' in sql
            return [row]
    result = asyncio.run(lib.approved_payload(Pool(), 'client', 4, 'op'))
    assert result[0]['audio_pcm_b64'] == 'AQA='
    row['audio_pcm'] = b'x'
    with pytest.raises(ValueError, match='повреждено'):
        asyncio.run(lib.approved_payload(Pool(), 'client', 4, 'op'))
