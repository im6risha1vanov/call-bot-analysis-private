from crm import callcenter_insights as ci


def attempt(phone, day, *, operator='olga', project='База A', live=False, decisions=None, friction=None, stop=False, item=1):
    return {'phone': phone, 'at': day + 'T12:00:00+03:00', 'operator': operator, 'project': project,
            'base': '2026-11-01', 'run_id': item, 'call_id': item, 'item_id': item, 'status': 'done',
            'check': {'live': live, 'decisions': decisions or [], 'friction': friction or [], 'stop_contact': stop}}


def decision(category, project='accounting', reason='Потребность', step=False, evidence=True):
    return {'project': project, 'category': category, 'reason': reason, 'next_step': step,
            'evidence': [{'at': 12, 'quote': 'да'}] if evidence else []}


def test_unique_phones_periods_and_denominators():
    rows = [attempt('9000000001','2026-11-17',live=True,decisions=[decision('qualified',step=True)]),
            attempt('9000000001','2026-11-18',operator='oksana',live=True),
            attempt('9000000002','2026-11-18'),
            attempt('9000000003','2026-11-15',live=True,decisions=[decision('reusable')])]
    result = ci.summarize(rows, '2026-11-17', '2026-11-18')
    assert result['current']['attempts'] == 3
    assert result['current']['phones'] == 2
    assert result['current']['reached'] == {'count': 1, 'denominator': 2, 'percent': 50.0}
    assert result['current']['qualified'] == {'count': 1, 'denominator': 1, 'percent': 100.0}
    assert result['previous']['phones'] == 1
    assert result['previous_start'] == '2026-11-15'


def test_cards_retain_categories_across_operators_and_days_but_accept_explicit_new_category_and_stop():
    rows = [attempt('9000000001','2026-11-17',decisions=[decision('qualified')]),
            attempt('9000000001','2026-11-18',operator='oksana',decisions=[decision('pending')],item=2),
            attempt('9000000001','2026-11-19',decisions=[decision('reusable', project='hh_ru')],stop=True,item=3)]
    cards = ci.build_cards(rows)
    assert {c['project']: c['category'] for c in cards} == {'accounting': 'qualified', 'hh_ru': 'reusable'}
    assert all(c['stop_contact'] for c in cards)
    assert all(len(c['history']) == 3 for c in cards)
    assert cards[0]['history_url'] == '#callcenter/:analysis:lead:9000000001'
    rows.append(attempt('9000000001','2026-11-20',decisions=[decision('competitor')],item=4))
    assert next(c for c in ci.build_cards(rows) if c['project']=='accounting')['category'] == 'competitor'


def test_friction_requires_distinct_phones_and_evidence():
    f = {'project':'hh_ru','step':'регистрация','issue':'не понимает','proposal':'уточнить','quote':'что это','at':5}
    rows = [attempt('9000000001','2026-11-17',friction=[f]), attempt('9000000001','2026-11-18',friction=[f],item=2)]
    assert not ci.summarize(rows,'2026-11-17','2026-11-18')['friction']
    rows.append(attempt('9000000002','2026-11-18',friction=[f],item=3))
    assert ci.summarize(rows,'2026-11-17','2026-11-18')['friction'][0]['leads'] == 2


def test_sheet_ambiguity_not_attributed():
    lead = {'phone':'79000000001', 'project':'A', 'base':'2026-01-01', 'attempts': []}
    index = ci._sheet_index({'olga': {'one':[lead], 'two':[dict(lead, project='B')]}})
    assert ci._attribute(index,'olga','9000000001','2026-11-17')['attribution'] == 'ambiguous'
    assert ci._attribute(index,'oksana','9000000001','2026-11-17')['attribution'] == 'unmatched'


def test_next_step_must_be_for_same_qualified_direction():
    rows = [attempt('9000000001', '2026-11-17', live=True, decisions=[decision('qualified', step=False),
                        decision('reusable', project='hh_ru', step=True)])]
    metric = ci.summarize(rows, '2026-11-17', '2026-11-17')['current']['next_step']
    assert metric == {'count': 0, 'denominator': 1, 'percent': 0.0}


def test_reason_counts_are_phone_unique_and_not_positive_decisions():
    rows = [attempt('9000000001', '2026-11-17', decisions=[decision('qualified')]),
            attempt('9000000002', '2026-11-17'), attempt('9000000002', '2026-11-17', item=3)]
    rows[1]['check']['state'] = rows[2]['check']['state'] = 'answering_machine'
    reasons = ci.summarize(rows, '2026-11-17', '2026-11-17')['reasons']
    assert len(reasons) == 1 and reasons[0]['reason'] == 'Автоответчик' and reasons[0]['leads'] == 1


def test_unauthorized_insights_never_query_pool():
    import asyncio
    from types import SimpleNamespace
    import pytest
    class ForbiddenPool:
        def __getattr__(self, name):
            raise AssertionError('query before authorization')
    who = SimpleNamespace(role='manager')
    with pytest.raises(PermissionError):
        asyncio.run(ci.dashboard(ForbiddenPool(), who))
    with pytest.raises(PermissionError):
        asyncio.run(ci.lead(ForbiddenPool(), who, '9000000001'))


def test_campaign_tab_base_operator_and_filter():
    rows = [attempt('9000000001','2026-11-17',project='B1_7495',live=True),
            attempt('9000000002','2026-11-17',project='B1_9200',operator='oksana',live=True)]
    rows[0]['tab'] = 'Картонная упаковка'
    rows[1]['tab'] = 'Бухгалтерские услуги'
    result = ci.summarize(rows,'2026-11-17','2026-11-17')
    assert {r['name'] for r in result['projects']} == {'Картонная упаковка','Бухгалтерские услуги'}
    assert {r['name'] for r in result['bases']} == {'B1_7495','B1_9200'}
    assert {r['name'] for r in result['operators']} == {'Ольга','Оксана'}
    result = ci.summarize(rows,'2026-11-17','2026-11-17',project='Картонная упаковка',base='B1_7495',operator='olga')
    assert result['current']['phones'] == 1
    assert result['combinations'][0]['project'] == 'Картонная упаковка'


def test_product_qualification_before_handoff_is_distinct_from_export_category():
    qualified_product = dict(decision('pending',step=False), product_qualified=True)
    later_pending = dict(decision('pending',step=False,evidence=False), product_qualified=False)
    rows = [attempt('9000000001','2026-11-17',live=True,decisions=[qualified_product]),
            attempt('9000000001','2026-11-18',live=True,decisions=[later_pending],item=2)]
    current = ci.summarize(rows,'2026-11-17','2026-11-18')['current']
    assert current['qualified']['count'] == 1
    assert current['next_step'] == {'count': 0, 'denominator': 1, 'percent': 0.0}
    assert current['category_counts']['qualified'] == 0


def test_fetch_records_bounds_company_period_and_phone_before_loading(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    class Pool:
        fetchval = AsyncMock(return_value=False)
        fetch = AsyncMock(return_value=[])
    pool = Pool()
    monkeypatch.delenv('CALLCENTER_LEADS_FOLDER_ID', raising=False)
    monkeypatch.setattr(ci.call_analysis_schema, 'ensure', AsyncMock())
    asyncio.run(ci.fetch_records(pool, 19, '2026-11-17', '2026-11-18', '9000000001'))
    query, *params = pool.fetch.await_args.args
    assert 'r.client_id=$1' in query and 'i.at >= $3 AND i.at < $4' in query
    assert params[0] == 19 and params[-1] == '9000000001'
    assert params[2].isoformat() == '2026-11-17T00:00:00+03:00'
    assert params[3].isoformat() == '2026-11-19T00:00:00+03:00'


def test_unclassified_includes_unknown_and_no_script():
    rows = [attempt('9000000001','2026-11-17'),
            attempt('9000000002','2026-11-17',decisions=[decision('pending')]),
            attempt('9000000003','2026-11-17',decisions=[decision('reusable')])]
    metrics = ci.summarize(rows,'2026-11-17','2026-11-17')['current']
    assert metrics['unclassified_phones'] == 2 and metrics['phones'] == 3


def test_automatic_batch_concurrency_is_bounded_and_requeues(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from crm import call_analysis as ca
    running, peak = 0, 0
    async def process_item(*args):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(.01)
        running -= 1
        return {'status':'done', 'stages':{}}
    class Pool:
        execute = AsyncMock(return_value='UPDATE 1')
        fetch = AsyncMock(return_value=[{'id':i} for i in range(4)])
        fetchval = AsyncMock(return_value=True)
    pool = Pool()
    monkeypatch.setattr(ca, '_process_item', process_item)
    run = {'id': 1, 'dedup_key': 'callcenter-monitor:olga:1:2026-11-17:v:auto',
           'client_id': 1, 'processed': 0, 'total_calls': 20}
    asyncio.run(ca.process(pool, run))
    assert peak == 4
    assert 'LIMIT 4' in pool.fetch.await_args.args[0]
    assert any("status='queued'" in c.args[0] for c in pool.execute.await_args_list)


def test_semantic_claim_failure_never_calls_provider(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from crm import call_analysis as ca, callcenter_verdict
    class Pool:
        execute = AsyncMock(return_value='UPDATE 0')
    pool = Pool()
    assess = AsyncMock()
    monkeypatch.setattr(callcenter_verdict, 'assess_semantic', assess)
    asyncio.run(ca._semantic_item(pool, {'id':1, 'stages': {'script_check': {'live':True}}, 'transcript':'x'}))
    assess.assert_not_awaited()


def test_sheet_competitor_provenance_conflict_and_revocation():
    marker = {'project':'accounting','marker':'конкурент','provenance':'sheet_operator','sheet_id':'x'}
    row = attempt('9000000001','2026-11-17',decisions=[decision('pending')])
    row['sheet_competitor'] = marker
    card = ci.build_cards([row])[0]
    assert card['category'] == 'competitor' and card['provenance'] == 'sheet_operator'
    row['check']['decisions'] = [decision('reusable')]
    assert ci.build_cards([row])[0]['category'] == 'reusable'
    row['check']['decisions'] = [decision('pending')]
    row['sheet_competitor'] = None
    assert ci.build_cards([row])[0]['category'] == 'pending'
    row['sheet_competitor'] = marker
    row['check']['stop_contact'] = True
    assert ci.build_cards([row])[0]['stop_contact']


def test_semantic_timeout_migration_retries_once_and_fences(monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock
    from crm import call_analysis as ca, callcenter_verdict
    class Pool:
        execute = AsyncMock(return_value='UPDATE 1')
    pool = Pool()
    monkeypatch.setattr(callcenter_verdict, '_roles', lambda lines: ('operator', {'client'}))
    monkeypatch.setattr(callcenter_verdict, 'assess_semantic', AsyncMock(side_effect=TimeoutError()))
    stages = {'script_check': {'live': True, 'decisions': [decision('pending')]},
              'semantic': {'status':'failed','attempts':3,'error':'TimeoutError'}}
    asyncio.run(ca._semantic_item(pool, {'id':1,'stages':stages,'transcript':'x'}))
    claim = json.loads(pool.execute.await_args_list[0].args[2])
    result = json.loads(pool.execute.await_args_list[1].args[2])
    assert claim['semantic']['status'] == 'processing' and claim['semantic']['timeout_migrated']
    assert result['semantic']['status'] == 'retry' and result['semantic']['attempts'] == 1
    assert pool.execute.await_args_list[1].args[3] == pool.execute.await_args_list[0].args[2]


def test_wrong_number_blocks_sheet_marker_on_same_moscow_day():
    marker = {'project':'accounting','marker':'конкурент','provenance':'sheet_operator','sheet_id':'x','tab':'Бухгалтерия'}
    row = attempt('9000000001','2026-11-17',decisions=[decision('pending')])
    row.update(sheet_competitor=marker, tab='Бухгалтерия', sheet_id='x', at='2026-11-16T22:30:00+00:00')
    wrong = attempt('9000000001','2026-11-17',item=2)
    wrong.update(tab='Бухгалтерия', sheet_id='x')
    wrong['check']['signals'] = ['wrong_number']
    assert ci.build_cards([row,wrong])[0]['category'] == 'pending'


def test_cards_reuses_passed_snapshot_without_refresh(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    class Pool:
        fetchval = AsyncMock(return_value=False)
        fetch = AsyncMock(return_value=[])
    snapshot = AsyncMock(side_effect=AssertionError('must reuse snapshot'))
    monkeypatch.setattr(ci.callcenter_base, 'snapshot', snapshot)
    monkeypatch.setattr(ci.call_analysis_schema, 'ensure', AsyncMock())
    assert asyncio.run(ci.cards(Pool(), 1, snapshot_data=('stamp', {'olga': {}, 'oksana': {}}))) == []
    snapshot.assert_not_awaited()
