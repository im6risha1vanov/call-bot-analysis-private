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

@pytest.mark.asyncio
async def test_warm_all_reuses_snapshot_and_never_mixes_operators():
    from datetime import date
    from crm import callcenter_insights as insights
    rows = [{'phone':'9991234567', 'operator':'olga', 'at':'2026-11-18T12:00:00+03:00',
             'run_id':1,'call_id':1,'item_id':1,'status':'done','duration_s':10,'check':{}, 'tab':'Упаковка', 'project':'B1'},
            {'phone':'9991234568', 'operator':'oksana', 'at':'2026-11-18T13:00:00+03:00',
             'run_id':2,'call_id':2,'item_id':2,'status':'done','duration_s':10,'check':{}, 'tab':'Упаковка', 'project':'B1'}]
    sent = []
    async def submit(pool, client, value):
        sent.append(value)
        return {'status':'queued'}
    with patch.object(insights, 'report_dates', return_value=(date(2026,11,17), date(2026,11,18))), \
         patch.object(insights, 'fetch_records', AsyncMock(return_value=rows)) as fetch, \
         patch.object(insights, 'selected_pins', AsyncMock(return_value=[])), patch.object(jobs, 'request', submit):
        states = await jobs.warm_all(object(), 1)
    assert set(states) == {'all','olga','oksana'}
    fetch.assert_awaited_once()
    assert [len(r['attempts']) for r in sent] == [2,1,1]
    assert sent[1]['attempts'][0]['operator'] == 'olga'
    assert sent[2]['attempts'][0]['operator'] == 'oksana'


@pytest.mark.asyncio
async def test_known_empty_operator_allowed_with_existing_project_filter():
    from datetime import date
    from crm import callcenter_insights as insights
    rows = [{'phone':'9991234567', 'operator':'olga', 'at':'2026-11-18T12:00:00+03:00',
             'run_id':1,'call_id':1,'item_id':1,'status':'done','duration_s':10,'check':{}, 'tab':'Упаковка', 'project':'B1'}]
    with patch.object(insights, 'report_dates', return_value=(date(2026,11,17), date(2026,11,18))), \
         patch.object(insights, 'selected_pins', AsyncMock(return_value=[])):
        result = await insights.report_for_company(None, 1, project='Упаковка', operator='oksana', _records=rows)
        assert result['filters']['operator'] == 'oksana'
        assert result['current']['attempts'] == 0 and result['attempts'] == []
        assert {o['id'] for o in result['options']['operators']} == {'olga', 'oksana'}
        empty = await insights.report_for_company(None, 1, project='Упаковка', base='Нет звонков в этой базе', operator='oksana', _records=rows)
        assert empty['attempts'] == [] and empty['filters']['base'] == 'Нет звонков в этой базе'
        with pytest.raises(ValueError):
            await insights.report_for_company(None, 1, project='Неизвестный проект', _records=rows)
        with pytest.raises(ValueError):
            await insights.report_for_company(None, 1, operator='not-a-person', _records=rows)


def test_personal_cache_version_keeps_overall_version():
    overall = report()
    operator = {**overall, 'filters': {'project':'', 'base':'', 'operator':'olga'}}
    old = jobs.OPERATOR_VERSION
    first = jobs.request_data(1, overall)[1]
    second = jobs.request_data(1, operator)[1]
    with patch.object(jobs, 'OPERATOR_VERSION', old + '-changed'):
        assert jobs.request_data(1, overall)[1] == first
        assert jobs.request_data(1, operator)[1] != second
    assert 'operator_script_candidates' not in jobs.request_data(1, overall)[2]['packet']
    assert 'operator_script_candidates' in jobs.request_data(1, operator)[2]['packet']

@pytest.mark.asyncio
async def test_highlights_are_read_only_scoped_and_redacted():
    from datetime import date
    from types import SimpleNamespace
    from crm import callcenter_insights as insights
    start, end = date(2026,11,12), date(2026,11,18)
    filters = {'project':'', 'base':'', 'operator':'olga'}
    key = jobs.scope_id(7, start.isoformat(), end.isoformat(), filters)
    rows = [{'scope_id':key, 'fingerprint':'new', 'result_fingerprint':'old', 'status':'queued',
             'result_at':datetime(2026,11,18,tzinfo=timezone.utc),
             'result': {'operator_review':{'script':{'status':'supported','observation':'Пример',
                           'action':'Уточнить','evidence':[{'phone':'79990000000'}], 'test':'лишнее'},
                           'base':{'status':'insufficient','observation':'','action':'', 'contact_fact':{'status':'supported','observation':'Неверный номер','action':'Проверить','evidence':[{'phone':'79991111111'}]}}}, 'history':['секрет']}}]
    class Pool:
        async def fetchval(self, sql): return True
        async def fetch(self, sql, tenant, ids):
            assert tenant == 7 and key in ids and len(ids) == 2
            assert 'client_id=$1' in sql and 'scope_id=ANY' in sql
            assert 'evidence' not in sql and 'report,' not in sql
            return rows
    actor = SimpleNamespace(role='owner', client_id=7)
    who = SimpleNamespace(role='director', actor=actor, company=7, test=False, support=False)
    with patch.object(insights, 'report_dates', return_value=(start,end)):
        out = await jobs.highlights_for_viewer(Pool(), who)
    assert (out['start'],out['end']) == ('2026-11-12','2026-11-18')
    assert out['operators']['olga']['status'] == 'stale'
    assert out['operators']['oksana']['status'] == 'waiting'
    assert out['operators']['olga']['brief']['operator_review']['script'] == {
        'status':'supported','observation':'Пример','action':'Уточнить'}
    fact = out['operators']['olga']['brief']['operator_review']['base']['contact_fact']
    assert fact == {'status':'supported','observation':'Неверный номер','action':'Проверить'}
    assert '79991111111' not in json.dumps(out)
    assert '79990000000' not in json.dumps(out)
    assert jobs.scope_id(8, start.isoformat(), end.isoformat(), filters) != key
    assert jobs.scope_id(7, '2026-11-11', end.isoformat(), filters) != key
    assert jobs.scope_id(7, start.isoformat(), end.isoformat(), {**filters,'base':'B1'}) != key


@pytest.mark.asyncio
async def test_highlights_forbidden_without_director_role_and_real_session():
    from types import SimpleNamespace
    class Pool:
        async def fetchval(self, *args): raise AssertionError('Forbidden request read the database')
    for who in (SimpleNamespace(role='manager',actor=SimpleNamespace(role='manager'),company=1,test=False,support=False),
                SimpleNamespace(role='director',actor=SimpleNamespace(role='owner'),company=1,test=True,support=False),
                SimpleNamespace(role='director',actor=SimpleNamespace(role='owner'),company=1,test=False,support=True),
                SimpleNamespace(role='director',actor=None,company=1,test=False,support=False)):
        with pytest.raises(PermissionError): await jobs.highlights_for_viewer(Pool(), who)


@pytest.mark.asyncio
async def test_highlights_waiting_if_cache_table_not_ready():
    from datetime import date
    from crm import callcenter_insights as insights
    class Pool:
        async def fetchval(self, sql): return False
        async def fetch(self, *args): raise AssertionError('Unexpected cache read')
    with patch.object(insights, 'report_dates', return_value=(date(2026,11,17),date(2026,11,18))):
        out = await jobs.operator_highlights(Pool(), 1)
    assert all(x['status'] == 'waiting' and x['brief'] is None for x in out['operators'].values())
