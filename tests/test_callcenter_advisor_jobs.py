import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from crm import callcenter_advisor_jobs as jobs


def report():
    return {'start': '2026-11-17', 'end': '2026-11-18', 'previous_start': '2026-11-15',
            'previous_end': '2026-11-16', 'filters': {'project': '', 'base': '', 'operator': ''},
            'current': {'coverage': {'dialogue': 0}, 'attempts': 1}, 'previous': {}, 'combinations': [],
            'reasons': [], 'findings': [], 'coaching': [], 'attempts': []}


def test_fingerprint_stable_and_scope_bound():
    r = report()
    scope, fingerprint, data = jobs.request_data(1, r)
    assert jobs.request_data(1, {**r, 'export_status': {'status': 'changed'}})[:2] == (scope, fingerprint)
    assert jobs.request_data(2, r)[0] != scope
    assert jobs.request_data(1, {**r, 'filters': {'project': 'упаковка', 'base': '', 'operator': ''}})[0] != scope
    assert jobs.request_data(1, {**r, 'current': {'coverage': {'dialogue': 0}, 'attempts': 2}})[1] != fingerprint
    assert set(data) == {'packet', 'sources'} and 'attempts' not in data


def test_stale_previous_result_is_visible_only_for_same_scope():
    row = {'fingerprint': 'new', 'result_fingerprint': 'old', 'status': 'queued',
           'result': json.dumps({'status': 'ready', 'summary': 'старые данные'}),
           'result_at': datetime(2026, 11, 18, tzinfo=timezone.utc)}
    state = jobs._result(row)
    assert state['status'] == 'stale' and state['brief']['summary'] == 'старые данные'
    row['status'] = 'error'
    assert jobs._result(row)['status'] == 'error' and jobs._result(row)['brief']
    row['result_fingerprint'] = 'new'; row['status'] = 'ready'
    assert jobs._result(row)['status'] == 'ready'


@pytest.mark.asyncio
async def test_retry_is_bounded_and_old_result_is_fenced():
    class Pool:
        def __init__(self): self.executed = []
        async def fetchval(self, *args): return True
        async def fetchrow(self, sql, *args):
            assert 'attempts<3' in sql and 'SKIP LOCKED' in sql
            return {'scope_id': 'scope', 'client_id': 1, 'fingerprint': 'old', 'report': json.dumps(jobs.request_data(1, report())[2]), 'attempts': 1}
        async def execute(self, sql, *args):
            self.executed.append((sql, args))
            if 'result_fingerprint=$3' in sql: return 'UPDATE 0'
            return 'UPDATE 1'
    pool = Pool()
    with patch.object(jobs.callcenter_advisor, 'advise_packet', AsyncMock(return_value={'status': 'ready'})):
        assert await jobs.work_once(pool)
    assert any("status='queued',token=NULL" in sql for sql, _ in pool.executed)
    assert any('fingerprint=$3' in sql for sql, _ in pool.executed)


@pytest.mark.asyncio
async def test_failed_attempt_gets_backoff_not_infinite_poll_retries():
    class Pool:
        def __init__(self): self.sql = []
        async def fetchval(self, *args): return True
        async def fetchrow(self, sql, *args):
            return {'scope_id': 'scope', 'client_id': 1, 'fingerprint': 'fp', 'report': json.dumps(jobs.request_data(1, report())[2]), 'attempts': 3}
        async def execute(self, sql, *args): self.sql.append(sql); return 'UPDATE 1'
    pool = Pool()
    with patch.object(jobs.callcenter_advisor, 'advise_packet', AsyncMock(side_effect=TimeoutError)):
        await jobs.work_once(pool)
    assert any("attempts>=3 THEN 'error'" in sql and "interval '30 minutes'" in sql for sql in pool.sql)

@pytest.mark.asyncio
async def test_selected_pins_use_only_current_confirmed_examples():
    from datetime import date
    from crm import callcenter_insights as insights
    class Pool:
        async def fetchval(self, *args): return True
        async def fetch(self, *args):
            return [dict(call_id=7, period_start=date(2026, 11, 17), period_end=date(2026, 11, 18),
                         script='packaging', stage_id='qualification', issue_code='unclear_question'),
                    dict(call_id=999, period_start=date(2026, 11, 17), period_end=date(2026, 11, 18),
                         script='packaging', stage_id='qualification', issue_code='unclear_question')]
    example = {'call_id': 7, 'run_id': 4, 'quote': 'Не понял вопрос', 'at': '00:15'}
    finding = {'project': 'packaging', 'stage_id': 'qualification', 'issue_code': 'unclear_question',
               'project_name': 'Упаковка', 'step': 'Уточнение', 'issue': 'Непонятный вопрос',
               'leads': 2, 'evidence': [example]}
    pins = await insights.selected_pins(Pool(), 1, {'findings': [finding]})
    assert len(pins) == 1 and pins[0]['evidence'] == [example]

@pytest.mark.asyncio
async def test_dashboard_keeps_archive_and_synthesis_uses_selected_pins():
    from types import SimpleNamespace
    from crm import callcenter_insights as insights
    scoped = report()
    scoped['advisor_pins'] = [{'start': '2026-11-17', 'evidence': []}]
    archived = [{'start': '2026-10-01', 'end': '2026-10-07', 'leads': 12, 'evidence': [{'call_id': 99}]}]
    queued = []
    async def submit(_pool, _client_id, data):
        queued.append(dict(data))
        return {'status': 'queued', 'brief': None}
    with patch.object(insights.call_analysis, 'can_manage', return_value=True), \
         patch.object(insights.call_analysis, 'company_of', return_value=1), \
         patch.object(insights, 'report_for_company', AsyncMock(return_value=dict(scoped))), \
         patch.object(insights, 'historical_findings', AsyncMock(return_value=archived)) as archive, \
         patch.object(jobs, 'request', submit):
        result = await insights.dashboard(object(), SimpleNamespace())
    assert result['historical_findings'] == archived
    assert queued[0]['advisor_pins'] == scoped['advisor_pins']
    assert 'historical_findings' not in queued[0]
    assert 'advisor_pins' not in result
    archive.assert_awaited_once()


def test_evidence_packet_never_receives_archive_pins():
    r = report()
    r['historical_findings'] = [{'start': '2026-10-01', 'evidence': [{'call_id': 99, 'quote': 'old'}]}]
    r['advisor_pins'] = []
    _, _, data = jobs.request_data(1, r)
    assert not data['packet']['pinned']
    assert not data['sources']
