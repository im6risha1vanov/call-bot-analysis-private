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


def test_daily_breakdown_coverage_and_repeat_across_days():
    rows = [attempt('9000000001','2026-11-17',live=True,decisions=[dict(decision('pending'), product_qualified=True)]),
            attempt('9000000001','2026-11-17',item=2),
            attempt('9000000001','2026-11-18',item=3),
            attempt('9000000002','2026-11-18',item=4)]
    rows[1]['check']['state'] = 'not_reached'
    rows[2]['status'] = 'audio_missing'
    rows[3]['status'] = 'pending'
    result = ci.summarize(rows, '2026-11-17', '2026-11-19')
    first, second, empty = result['daily']
    assert first['phones'] == 1 and first['attempts'] == 2
    assert first['qualified']['count'] == 1 and first['next_step']['count'] == 0
    assert second['phones'] == 2 and empty['attempts'] == 0
    assert result['current']['phones'] == 2
    assert result['current']['coverage'] == {'dialogue':1, 'missing_or_unclear':2, 'no_answer_or_machine':1}
    assert sum(result['current']['coverage'].values()) == result['current']['attempts']
    assert result['previous']['attempts'] == 0


def test_dashboard_defaults_to_moscow_last_seven_days_and_preserves_explicit_dates(monkeypatch):
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = cls(2026, 11, 18, 22, 30, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(ci, 'datetime', FakeDateTime)
    monkeypatch.setattr(ci.call_analysis, 'can_manage', lambda who: True)
    monkeypatch.setattr(ci, 'fetch_records', AsyncMock(return_value=[]))
    monkeypatch.setenv('CALLCENTER_MONITOR_FROM', '2026-11-11T00:00:00+03:00')
    monkeypatch.delenv('CALLCENTER_EXPORT_SHEETS', raising=False)
    who = SimpleNamespace(company=1)
    default = asyncio.run(ci.dashboard(None, who))
    assert (default['start'], default['end']) == ('2026-11-13', '2026-11-19')
    explicit = asyncio.run(ci.dashboard(None, who, '2026-11-11', '2026-11-12'))
    assert (explicit['start'], explicit['end']) == ('2026-11-11', '2026-11-12')
    monkeypatch.setenv('CALLCENTER_MONITOR_FROM', '2026-11-17T00:00:00+03:00')
    assert asyncio.run(ci.dashboard(None, who))['start'] == '2026-11-17'


def test_friction_group_uses_broad_label_and_preserves_each_examples_context():
    first = {'project':'packaging', 'step':'Квалификация', 'stage_id':'qualification', 'issue_code':'unclear_question',
             'issue':'Не понятен вопрос о расходах', 'proposal':'Пояснить расходы', 'measure':'Доля ответов на вопрос о расходах', 'quote':'Что за сумма?', 'at':5}
    second = dict(first, issue='Не понятен вопрос о печати', proposal='Уточнить печать',
                  measure='Доля ответов о печати', quote='Какая печать?')
    rows = [attempt('9000000001','2026-11-17',friction=[first]),
            attempt('9000000002','2026-11-17',friction=[second],item=2)]
    group = ci.summarize(rows, '2026-11-17', '2026-11-17')['friction'][0]
    assert group['issue'] == 'Вопрос непонятен' and group['leads'] == 2
    assert 'proposal' not in group and 'measure' not in group
    assert [(x['issue'], x['proposal'], x['measure']) for x in group['evidence']] == [
        (first['issue'], first['proposal'], first['measure']), (second['issue'], second['proposal'], second['measure'])]


def test_findings_rank_by_distinct_phones_and_human_project_preserve_singles():
    f = {'project':'packaging', 'step':'Квалификация', 'stage_id':'qualification', 'issue_code':'unclear_question',
         'issue':'Вопрос клиента', 'proposal':'Пояснить', 'measure':'Ответы', 'quote':'Что?', 'at':10}
    one = attempt('9000000001','2026-11-17',friction=[f])
    same = attempt('9000000001','2026-11-17',friction=[f],item=2)
    different = attempt('9000000002','2026-11-17',friction=[f],item=3)
    single = attempt('9000000003','2026-11-17',friction=[dict(f, issue_code='price',issue='Вопрос цены')],item=4)
    findings = ci.summarize([one,same,different,single], '2026-11-17','2026-11-17')['findings']
    assert [f['leads'] for f in findings] == [2,1]
    assert findings[0]['project_name'] == 'Картонная упаковка'
    assert findings[0]['evidence'][0]['date'] == '2026-11-17'


def test_pinned_finding_uses_tenant_selector_real_period_and_filters(monkeypatch):
    import asyncio
    from datetime import date
    from unittest.mock import AsyncMock
    target = {'project':'packaging','step':'Квалификация','stage_id':'qualification','issue_code':'unclear_question',
              'issue':'Нерегулярные закупки','proposal':'Проверить три месяца','measure':'Доля ответов', 'quote':'Покупаем по мере нужды','at':20}
    other = dict(target, issue_code='price',issue='Другое замечание')
    one = attempt('9000000001','2026-11-17',friction=[target,other],item=5)
    one['tab'] = 'Картонная упаковка'
    another = attempt('9000000002','2026-11-16',friction=[target],item=6)
    another['tab'] = 'Картонная упаковка'
    pins = [{'call_id':5,'period_start':date(2026,11,11),'period_end':date(2026,11,17),
             'script':'packaging','stage_id':'qualification','issue_code':'unclear_question'}]
    class Pool:
        fetchval = AsyncMock(return_value=True)
        fetch = AsyncMock(return_value=pins)
    pool = Pool()
    read = AsyncMock(return_value=[one,another])
    monkeypatch.setattr(ci, 'fetch_records', read)
    results = asyncio.run(ci.historical_findings(pool, 42))
    assert len(results) == 1 and results[0]['leads'] == 2
    assert results[0]['evidence'][0]['quote'] == 'Покупаем по мере нужды'
    assert pool.fetch.await_args.args[1] == 42 and read.await_args.args[1] == 42
    assert not asyncio.run(ci.historical_findings(pool,42,operator='oksana'))
    assert not asyncio.run(ci.historical_findings(pool,42,project='Другое'))
    assert not asyncio.run(ci.historical_findings(pool,42,base='Другая база'))


def test_wrong_number_proof_requires_unambiguous_client_and_specific_phrase():
    from crm.callcenter_insights import client_wrong_number_evidence as evidence
    text = '[00:01 Оператор] Здравствуйте.\n[00:05 Клиент] Вы ошиблись номером, до свидания.'
    assert evidence(text) == [{'quote':'Вы ошиблись номером', 'at':5, 'kind':'wrong_number'}]
    assert evidence('[00:01 Оператор] Вы ошиблись номером.\n[00:05 Клиент] Здравствуйте.') == []
    assert evidence('[00:01 Спикер 1] Здравствуйте.\n[00:05 Спикер 2] Вы ошиблись номером.') == []
    assert evidence('[00:01 Оператор] Здравствуйте.\n[00:05 Клиент] Вы ошиблись с городом.') == []
    assert evidence('[00:01 Оператор] Здравствуйте.\n[00:05 Клиент] Вы не ошиблись номером.') == []
    assert evidence('[00:01 Оператор] Здравствуйте.\n[00:05 Клиент] Не тот номер.\n[00:06 Спикер 3] Да.') == []


def test_wrong_number_proof_is_attached_without_transcript(monkeypatch):
    import asyncio
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock
    row = dict(id=1, call_id=2, phone='79991234567', at=datetime(2026, 11, 18, tzinfo=timezone.utc),
               stages={'script_check':{'signals':['wrong_number']}}, operator='olga', analysis_run_id=3,
               status='done', duration_s=8, transcript_source='existing', error=None, mango_direction=2, mango_result={},
               source_transcript='[00:01 Оператор] Здравствуйте.\n[00:03 Клиент] Вы ошиблись номером.')
    class Pool:
        fetchval = AsyncMock(return_value=False)
        fetch = AsyncMock(return_value=[row])
    monkeypatch.setattr(ci.call_analysis_schema, 'ensure', AsyncMock())
    monkeypatch.delenv('CALLCENTER_LEADS_FOLDER_ID', raising=False)
    result = asyncio.run(ci.fetch_records(Pool(), 1))
    assert result[0]['check']['client_evidence'][0]['quote'] == 'Вы ошиблись номером'
    assert 'source_transcript' not in result[0]


def test_contact_stats_use_mango_and_unique_phones_without_inventing_no_answer():
    def call(phone, stamp, status, *, talk='0', answer=None, check=None):
        return {'phone': phone, 'at': stamp, 'mango_direction': 2, 'check': check or {},
                'mango_result': {'context_status':status, 'talk_duration':talk, 'answers':[answer]}}
    rows = [call('79990000001','2026-11-18T08:00:00+03:00','0'),
            call('89990000001','2026-11-18T09:00:00+03:00','1',answer='123'),
            call('9990000002','2026-11-18T08:00:00+03:00','1',talk='15',check={'state':'answering_machine'}),
            call('9990000003','2026-11-18T08:00:00+03:00','0'),
            call('9990000004','2026-11-18T08:00:00+03:00',None),
            call('9990000005','2026-11-18T08:00:00+03:00','0',talk='9'),
            call('9990000006','2026-11-18T08:00:00+03:00','1',talk='0',answer='123')]
    stats = ci.contact_stats(rows)
    assert stats['dialed'] == 6 and stats['attempted_calls'] == 7
    assert stats['reached'] == 3 and stats['automated_only'] == 1
    assert stats['no_answer_only'] == 1 and stats['unknown'] == 2
    assert stats['repeat_attempts'] == 1 and stats['reached_after_no_answer'] == 1
    assert stats['reached'] + stats['no_answer_only'] + stats['unknown'] == stats['dialed']


def test_contact_stats_only_outgoing_and_never_order_equal_timestamps():
    def call(status, direction=2):
        return {'phone':'9990000001','at':'2026-11-18T10:00:00+03:00','mango_direction':direction,
                'mango_result':{'context_status':status},'check':{}}
    stats = ci.contact_stats([call('0'), call('1'), call('1', 1)])
    assert stats['attempted_calls'] == 2 and stats['repeat_attempts'] == 1
    assert stats['reached'] == 1 and stats['reached_after_no_answer'] == 0


def test_base_comparison_requires_unique_matching_source():
    def call(phone, project, tab='Упаковка', attribution='matched'):
        return {'phone':phone,'mango_direction':2,'mango_result':{'context_status':'0'},
                'check':{},'tab':tab,'project':project,'attribution':attribution}
    current = [call('9990000001','B1'),call('9990000002','B1'),call('9990000002','B2'),
               call('9990000003','B1',attribution='unmatched')]
    previous = [call('9990000004','B1')]
    groups = ci.contact_stats_by_base(current, previous)
    assert len(groups) == 1 and groups[0]['name'] == 'B1' and groups[0]['dialed'] == 1
    assert groups[0]['previous']['dialed'] == 1
    assert ci.contact_stats_by_base(current, [call('9990000004','other')])[0]['previous'] is None


def test_reached_after_no_answer_only_if_first_success_follows_first_failure():
    def call(stamp, status):
        return {'phone':'9990000001', 'at':stamp, 'mango_direction':2,
                'mango_result':{'context_status':status}, 'check':{}}
    earlier = '2026-11-18T09:00:00+03:00'
    middle = '2026-11-18T10:00:00+03:00'
    later = '2026-11-18T11:00:00+03:00'
    assert ci.contact_stats([call(earlier,'1'),call(middle,'0'),call(later,'1')])['reached_after_no_answer'] == 0
    assert ci.contact_stats([call(earlier,'0'),call(middle,'1'),call(later,'1')])['reached_after_no_answer'] == 1
    assert ci.contact_stats([call(None,'1'),call(middle,'0'),call(later,'1')])['reached_after_no_answer'] == 0
