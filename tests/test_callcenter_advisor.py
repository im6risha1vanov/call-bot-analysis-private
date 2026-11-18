import asyncio
import importlib
import json
import pytest

from crm import callcenter_advisor as advisor


def report():
    evidence = {'phone':'9991112233', 'run_id':7, 'call_id':21, 'quote':'Покажите пример расчёта', 'at':19,
                'step':'Стоимость', 'issue':'Непонятна цена', 'proposal':'Объяснить расчёт', 'measure':'Согласие на разговор'}
    return {'start':'2026-11-17', 'end':'2026-11-18', 'filters':{'project':'Упаковка'},
            'current':{'coverage':{'dialogue':2}, 'attempts':2, 'phones':2}, 'previous':{},
            'combinations':[], 'reasons':[],
            'findings':[{'project_name':'Упаковка','issue_code':'price','step':'Цена','issue':'Цена', 'leads':1,'evidence':[evidence]}],
            'coaching':[], 'attempts':[{'call_id':21,'run_id':7,'phone':'9991112233','check':{'friction':[evidence]}}]}


def response(ref='E1'):
    return {'summary':'Клиент просит объяснить расчёт.', 'actions':[{'title':'Уточнить расчёт',
            'diagnosis':'Клиенту нужен пример.', 'action':'Сказать: «Уточню параметры и подготовим пример».',
            'test':'Сравнить согласия до и после на сопоставимых звонках.', 'kind':'script','refs':[ref]}],
            'limitations':['Один пример не доказывает повторяемость.']}


def test_packet_scoped_and_private():
    r=report(); r['historical_findings']=[{'evidence':[{'phone':'9994445566','quote':'Чужая цитата'}]}]
    r['filters']['base'] = 'База +7 (999) 444-55-66'
    r['findings'][0]['evidence'].append({'call_id':99,'run_id':10,'phone':'9994445566','quote':'Чужая цитата','at':1})
    packet, sources = advisor.evidence_packet(r)
    text=json.dumps(packet, ensure_ascii=False)
    assert '9991112233' not in text and '9994445566' not in text and '444-55-66' not in text
    assert 'Чужая цитата' not in text and len(sources)==1
    assert sources['E1']['phone']=='9991112233'


def test_rejects_unknown_references_and_unsupported_numbers():
    packet, sources=advisor.evidence_packet(report())
    with pytest.raises(advisor.SynthesisError):
        advisor.validate(response('E99'),packet,sources)
    data=response(); data['actions'][0]['diagnosis']='Так сказали 987 клиентов.'
    with pytest.raises(advisor.SynthesisError):
        advisor.validate(data,packet,sources)
    valid=advisor.validate(response(),packet,sources)
    assert valid['status']=='ready' and valid['actions'][0]['evidence'][0]['call_id']==21


def test_transport_and_low_support(monkeypatch):
    monkeypatch.setenv('CVC_API_KEY', 'synthetic-test')
    transport=importlib.import_module('crm.callcenter_transport'); calls=[]
    async def fake(*args, **kwargs):
        calls.append((args, kwargs)); return response(), 0
    monkeypatch.setattr(transport, 'request', fake)
    r=report(); r['current']['coverage']['dialogue']=1
    assert asyncio.run(advisor.advise(r))['status']=='insufficient' and not calls
    r['current']['coverage']['dialogue']=2
    assert asyncio.run(advisor.advise(r))['status']=='ready'
    args, kwargs=calls[0]
    assert args[1].startswith('Верни JSON по инструкции.\n') and '9991112233' not in args[1]
    assert kwargs['json_object'] is True and kwargs['reasoning_effort']=='low'


def test_pin_must_resolve_to_selected_source():
    r=report()
    r['historical_findings']=[{'project_name':'Упаковка', 'evidence':[r['findings'][0]['evidence'][0],
        {'run_id':8, 'call_id':99, 'quote':'Другой период', 'at':10}]}]
    packet, sources=advisor.evidence_packet(r)
    assert len(packet['pinned'])==1 and len(sources)==1


def test_advice_can_run_from_prepared_packet(monkeypatch):
    monkeypatch.setenv('CVC_API_KEY', 'synthetic-test')
    transport=importlib.import_module('crm.callcenter_transport'); calls=[]
    async def fake(*args, **kwargs):
        calls.append((args, kwargs)); return response(), 0
    monkeypatch.setattr(transport, 'request', fake)
    packet, sources=advisor.evidence_packet(report())
    assert asyncio.run(advisor.advise_packet(packet, sources))['status']=='ready'
    assert calls and '9991112233' not in calls[0][0][1]
    packet['current']['coverage']['dialogue']=1
    assert asyncio.run(advisor.advise_packet(packet, sources))['status']=='insufficient'
    assert len(calls)==1


def test_compact_summary_refs_comparable_and_proposed_duration():
    packet, sources=advisor.evidence_packet(report())
    data=response()
    data['summary']='72 из 1261 телефонов. Нужно улучшить скрипт.'
    data['actions'][0]['title']='Уточнить срок E15'
    data['actions'][0]['action']='Спросить: «Планируете покупку в ближайшие 3 месяца?» (E19)'
    data['actions'][0]['test']='Сравнить долю согласий на следующую беседу.'
    out=advisor.validate(data,packet,sources)
    assert out['status']=='ready' and '72' not in out['summary']
    text=json.dumps(out,ensure_ascii=False)
    assert 'E15' not in text and 'E19' not in text and '3 месяца' in text
    assert 'одного проекта и одной базы' in out['actions'][0]['test']


def test_valid_remaining_action_and_no_mixed_benchmark():
    packet, sources=advisor.evidence_packet(report())
    data=response()
    bad={**data['actions'][0], 'test':'Сравнить с количеством всех диалогов.'}
    data['actions']=[bad,data['actions'][0]]
    assert len(advisor.validate(data,packet,sources)['actions'])==1


def test_retries_invalid_synthesis_and_fails_visibly(monkeypatch):
    monkeypatch.setenv('CVC_API_KEY','synthetic-test')
    transport=importlib.import_module('crm.callcenter_transport'); calls=[]
    async def fake(*args,**kwargs):
        calls.append(args[1]); return (response('E999') if len(calls)==1 else response()), 0
    monkeypatch.setattr(transport,'request',fake)
    packet,sources=advisor.evidence_packet(report())
    assert asyncio.run(advisor.advise_packet(packet,sources))['status']=='ready'
    assert len(calls)==2 and 'Исправь прошлый ответ' in calls[1]
    async def fail(*args,**kwargs): return response('E999'),0
    monkeypatch.setattr(transport,'request',fail)
    with pytest.raises(advisor.SynthesisError): asyncio.run(advisor.advise_packet(packet,sources))


def personal_report():
    r=report(); r['filters']['operator']='olga'; r['current']['coverage']['dialogue']=1
    coach={'step':'Начало разговора','advice':'Не утверждать наличие заявки', 'quote':'Вы оставляли заявку', 'at':3}
    wrong={'issue':'Неверный номер','proposal':'Проверить контакт','quote':'Вы ошиблись номером','at':8}
    row={'call_id':30,'run_id':9,'phone':'9993332211','operator':'olga',
         'project':'Картон','tab':'Картон','attribution':'ambiguous',
         'check':{'coaching':[coach],'friction':[wrong]}}
    another={'call_id':31,'run_id':10,'phone':'9993332222','operator':'oksana',
             'attribution':'matched','check':{'coaching':[coach]}}
    r['attempts']=[row,another]; r['coaching']=[]; r['findings']=[]
    return r


def test_personal_candidates_are_scoped_and_independent():
    packet,sources=advisor.evidence_packet(personal_report())
    script=packet['operator_script_candidates']; base=packet['operator_base_candidates']
    assert len(script)==1 and len(base)==1 and len(sources)==2
    assert all(x['call_id']==30 for x in sources.values())
    payload={'operator_review':{
        'script':{'status':'supported','observation':'В начале разговора оператор сообщил о заявке.',
                  'action':'Проверять источник обращения.', 'test':'Проверить записи разговоров.', 'refs':[script[0]['ref']]},
        'base':{'status':'supported','observation':'Собеседник сообщил о неверном номере.',
                'action':'Проверить этот контакт.', 'test':'Проверить результат обновления.', 'refs':[base[0]['ref']]}}}
    out=advisor.validate_personal(payload,packet,sources)
    assert out['status']=='ready' and out['operator_review']['script']['status']=='supported'
    assert out['operator_review']['script']['evidence'][0]['step']=='Начало разговора'
    assert 'привязка' in out['operator_review']['base']['contact_fact']['observation'].lower()
    assert 'в этой базе' not in out['operator_review']['base']['contact_fact']['observation'].lower()
    payload['operator_review']['script']['refs']=[base[0]['ref']]
    with pytest.raises(advisor.SynthesisError): advisor.validate_personal(payload,packet,sources)


def test_refusal_and_late_callback_are_not_base_defects():
    r=personal_report(); row=r['attempts'][0]
    row['check']['friction']=[{'quote':'Сейчас неудобно, перезвоните','at':8},
                              {'quote':'Мы покупаем коробки нерегулярно','at':9},
                              {'quote':'Повторите вопрос','at':10}]
    packet,sources=advisor.evidence_packet(r)
    assert packet['operator_base_candidates']==[]
    out=advisor.validate_personal({'operator_review':{}},packet,sources)
    assert out['operator_review']['base']['status']=='insufficient'
    assert 'статистика' in out['operator_review']['base']['observation'].lower()


def test_one_dialog_personal_call_not_blocked(monkeypatch):
    monkeypatch.setenv('CVC_API_KEY','synthetic-test')
    transport=importlib.import_module('crm.callcenter_transport'); calls=[]
    packet,sources=advisor.evidence_packet(personal_report())
    ref=packet['operator_script_candidates'][0]['ref']
    async def fake(*args,**kwargs):
        calls.append(args); return {'operator_review':{'script':{'status':'supported','observation':'Оператор назвал заявку.',
            'action':'Проверить источник.', 'test':'Проверить реплики.', 'refs':[ref]}, 'base':{'status':'insufficient'}}},0
    monkeypatch.setattr(transport,'request',fake)
    out=asyncio.run(advisor.advise_packet(packet,sources))
    assert out['operator_review']['script']['status']=='supported' and calls
    assert out['operator_review']['base']['status']=='insufficient'


def test_product_mismatch_is_direct_not_wrong_number_or_whole_base():
    r=personal_report(); row=r['attempts'][0]; row['attribution']='matched'
    e1={'quote':'Нет, вообще не гофра. Однослойный картон нужен', 'at':12}
    e2={'quote':'Я покупаю в месяц на 5000 рублей коробки', 'at':14}
    row['check']['decisions']=[{'project':'packaging','category':'reusable','evidence':[e1,e2]}]
    packet,sources=advisor.evidence_packet(r)
    c=packet['operator_base_candidates']
    mismatch=[x for x in c if x['kind']=='product_mismatch']
    assert len(mismatch)==2
    ref=next(x['ref'] for x in mismatch if sources[x['ref']]['quote']==e2['quote'])
    payload={'operator_review':{'base':{'status':'supported','observation':'Клиент закупает на 5000 рублей в месяц.',
        'action':'Уточнять закупку.', 'test':'Проверить отбор.', 'refs':[ref]}}}
    out=advisor.validate_personal(payload,packet,sources)['operator_review']['base']['contact_fact']
    assert out['status']=='supported' and out['observation']=='Клиент закупает на 5000 рублей в месяц.'
    assert out['evidence'][0]['quote']==e2['quote'] and out['evidence'][0]['kind']=='product_mismatch'
    assert out['evidence'][0]['base']=='Картон'


def test_team_packet_keeps_existing_shape():
    packet,_=advisor.evidence_packet(report())
    assert 'operator_script_candidates' not in packet
    assert 'operator_base_candidates' not in packet and 'ambiguous_attribution' not in packet


def test_other_campaigns_and_client_quote_are_supported_without_ambiguous_hh():
    r=personal_report(); row=r['attempts'][0]; row['attribution']='matched'
    row['project']='База B1_79991234567'; row['tab']='Ангары'
    items=[('hangars','reusable','Нужен тентовый ангар'),
           ('hh_ru','reusable','Мы уже регистрировались на hh.ru'),
           ('hh_ru','reusable','Возможно мы уже регистрировались'),
           ('accounting','competitor','Мы оказываем бухгалтерские услуги клиентам')]
    row['check']['decisions']=[{'project':project,'category':cat,'evidence':[{'quote':quote,'at':i}]} for i,(project,cat,quote) in enumerate(items)]
    row['check']['client_evidence']=[{'quote':'Вы ошиблись номером','at':21,'kind':'wrong_number'}]
    packet,sources=advisor.evidence_packet(r)
    candidates=packet['operator_base_candidates']
    quotes=[sources[x['ref']]['quote'] for x in candidates]
    assert 'Нужен тентовый ангар' in quotes and 'Мы уже регистрировались на hh.ru' in quotes
    assert 'Возможно мы уже регистрировались' not in quotes
    assert 'Мы оказываем бухгалтерские услуги клиентам' in quotes and 'Вы ошиблись номером' in quotes
    assert '79991234567' not in json.dumps(packet,ensure_ascii=False)
    item=next(x for x in candidates if sources[x['ref']]['quote']=='Нужен тентовый ангар')
    data={'operator_review':{'base':{'status':'supported','observation':'Клиенту нужен тентовый ангар.',
         'action':'Уточнять конструкцию.', 'test':'Проверить разметку.', 'refs':[item['ref']]}}}
    out=advisor.validate_personal(data,packet,sources)['operator_review']['base']['contact_fact']
    assert out['observation']=='Клиенту нужен тентовый ангар.'
    assert out['action']=='Уточнять конструкцию.' and out['evidence'][0]['base']=='База B1_79991234567'


def test_personal_sampling_bounded_diverse_and_recent():
    r=personal_report(); r['attempts']=[]
    for i in range(60):
        project='Упаковка' if i < 48 else 'Ангары'
        step='Начало' if i < 36 else 'Квалификация'
        quote=f'Оператор сказал вариант номер {i}'
        r['attempts'].append({'call_id':i+1,'run_id':1,'phone':'9993332211','operator':'olga',
            'tab':project,'at':f'2026-11-{10+i//10:02d}T10:00:00', 'attribution':'matched',
            'check':{'coaching':[{'step':step,'advice':'Уточнить вопрос', 'quote':quote,'at':2}]}})
    packet,sources=advisor.evidence_packet(r)
    assert len(sources)<=12 and len(packet['examples'])<=12
    candidates=packet['operator_script_candidates']
    assert {c['project'] for c in candidates} == {'Упаковка','Ангары'}
    assert {c['step'] for c in candidates} == {'Начало','Квалификация'}
    assert max(int(x['call_id']) for x in sources.values())>=50
    assert any(c['distinct_calls']>=10 for c in candidates)
    assert not packet['findings'] and not packet['coaching'] and not packet['outcomes']
    reversed_packet,reversed_sources=advisor.evidence_packet(dict(r,attempts=list(reversed(r['attempts']))))
    assert {x['call_id'] for x in sources.values()}=={x['call_id'] for x in reversed_sources.values()}


def test_wrong_number_strict_for_all_evidence_paths():
    r=personal_report(); row=r['attempts'][0]; row['check']['coaching']=[]
    cases=['Вы ошиблись с городом', 'Вы ошиблись', 'Вы не ошиблись номером']
    row['check']['friction']=[{'quote':x,'at':i} for i,x in enumerate(cases)]
    row['check']['client_evidence']=[{'quote':x,'at':i+5} for i,x in enumerate(cases)]
    row['check']['decisions']=[{'project':'packaging','category':'pending',
        'evidence':[{'quote':x,'at':i+10} for i,x in enumerate(cases)]}]
    packet,sources=advisor.evidence_packet(r)
    assert not packet['operator_base_candidates'] and not sources


def test_asr_only_claim_is_not_a_confirmed_operator_deviation():
    r=personal_report(); row=r['attempts'][0]
    row['check']['coaching']=[{'step':'Вопрос','advice':'Неразборчивая формулировка',
                               'quote':'ШЕригами мапс','at':2}]
    row['check']['friction']=[]
    packet,sources=advisor.evidence_packet(r)
    assert not packet['operator_script_candidates']
    out=asyncio.run(advisor.advise_packet(packet,sources))
    assert out['operator_review']['script']['status']=='insufficient'


def test_asr_only_output_retries_when_real_action_available():
    r=personal_report(); row=r['attempts'][0]
    row['check']['coaching']=[
      {'step':'Вопрос','advice':'Уточнить вопрос', 'quote':'Заняли буквально коробки. Я','at':2},
      {'step':'Квалификация','advice':'Разделить составной вопрос', 'quote':'Размер и тираж, сколько?', 'at':3}]
    packet,sources=advisor.evidence_packet(r)
    candidates=packet['operator_script_candidates']
    assert sources[candidates[0]['ref']]['quote']=='Размер и тираж, сколько?'
    noisy=next(x for x in candidates if sources[x['ref']]['quote'].startswith('Заняли'))
    payload={'operator_review':{'script':{'status':'supported','observation':'Фраза неразборчива и оборвана.',
      'action':'Говорить чётче.', 'test':'Прослушать запись.', 'refs':[noisy['ref']]}}}
    with pytest.raises(advisor.SynthesisError): advisor.validate_personal(payload,packet,sources)


def test_existing_brief_clean_is_idempotent_and_does_not_invent_quotes():
    brief={'status':'ready','summary':'Старый текст E1', 'operator_review':{
      'script':{'status':'supported','observation':'Оператор уточнил «1000 штук — это самосборные».',
        'action':'Проверить контакт из E1 и зафиксировать несоответствие.',
        'test':'Проверить пример по E1.',
        'evidence':[{'quote':'1000 штук это самобурные.', 'step':'Уточнение параметров'}]},
      'base':{'status':'insufficient','observation':'Недостаточно данных.', 'action':'', 'test':'', 'evidence':[]}}}
    out=advisor.clean_personal_brief(brief)
    script=out['operator_review']['script']
    assert '1000 штук — это самосборные' not in script['observation']
    assert script['action']=='Проверить контакт и зафиксировать несоответствие.'
    assert 'E1' not in json.dumps(out,ensure_ascii=False)
    assert '1000 штук это самобурные.' == script['evidence'][0]['quote']
    assert brief['summary']=='Старый текст E1' and out==advisor.clean_personal_brief(out)


def test_exact_quotes_and_step_names_remain_in_personal_observation():
    brief={'operator_review':{'script':{'status':'supported',
        'observation':'В шаге «Уточнение параметров» прозвучало «Какой размер?».',
        'action':'Уточнить размер.', 'test':'Проверить диалоги.',
        'evidence':[{'quote':'Какой размер?','step':'Уточнение параметров'}]}}}
    out=advisor.clean_personal_brief(brief)
    assert out['operator_review']['script']['observation']==brief['operator_review']['script']['observation']


def test_unverified_trailing_quote_removed_without_dangling_phrase():
    brief={'operator_review':{'script':{'status':'supported',
        'observation':'Оператор подменяет ответ клиента предположением: «1000 штук — это самосборные».',
        'action':'Уточнять ответ.', 'test':'Проверить диалоги.',
        'evidence':[{'quote':'1000 штук это самобурные.', 'step':'Уточнение'}]}}}
    out=advisor.clean_personal_brief(brief)
    assert out['operator_review']['script']['observation']=='Оператор подменяет ответ клиента предположением.'
    assert out['summary']=='Оператор подменяет ответ клиента предположением.'
    assert out==advisor.clean_personal_brief(out)
    brief['operator_review']['script']['observation']='Оператор сказал: «1000 штук — это самосборные».'
    out=advisor.clean_personal_brief(brief)
    assert out['operator_review']['script']['observation']=='В доказательстве приведена исходная реплика.'


def phone_stats(**overrides):
    values={'dialed':10,'reached':4,'no_answer_only':4,'automated_only':1,'unknown':2,
            'attempted_calls':16,'repeat_attempts':6,'reached_after_no_answer':2}
    values.update(overrides); return values


def test_telephony_stats_work_without_a_single_transcript():
    r=personal_report(); r['attempts']=[]; r['contact_stats']=phone_stats()
    packet,sources=advisor.evidence_packet(r)
    assert not sources and packet['contact_stats']==r['contact_stats']
    brief=asyncio.run(advisor.advise_packet(packet,sources))
    base=brief['operator_review']['base']
    assert base['status']=='supported' and base['evidence']==[]
    assert 'Контактов в работе: 10' in base['observation'] and 'подтверждено: 4 (40%)' in base['observation']
    assert 'неуспешные попытки — 4' in base['observation'] and 'не установлен — 2' in base['observation']
    assert 'номерам: 2' in base['observation']
    assert 'не всегда означает подтверждённый разговор' in base['observation']
    assert base['contact_fact']['status']=='insufficient'


def test_unknown_only_and_invalid_stats_do_not_invent_reachability():
    packet,sources=advisor.evidence_packet(personal_report())
    packet['contact_stats']=phone_stats(dialed=2,reached=0,no_answer_only=0,automated_only=0,unknown=2,attempted_calls=2,repeat_attempts=0,reached_after_no_answer=0)
    out=advisor.validate_personal({'operator_review':{}},packet,sources)['operator_review']['base']
    assert out['status']=='insufficient' and 'Исход соединения по ним не установлен' in out['observation'] and '%' not in out['observation']
    packet['contact_stats']['unknown']=3
    out=advisor.validate_personal({'operator_review':{}},packet,sources)['operator_review']['base']
    assert out['status']=='insufficient' and 'недоступна' in out['observation']


def test_statistics_and_specific_contact_fact_remain_separate():
    r=personal_report();r['contact_stats']=phone_stats()
    packet,sources=advisor.evidence_packet(r); ref=packet['operator_base_candidates'][0]['ref']
    data={'operator_review':{'base':{'status':'supported','observation':'Собеседник сообщил о неверном номере.',
            'action':'Проверить карточку.', 'test':'Сверить контакт.', 'refs':[ref]}}}
    base=advisor.validate_personal(data,packet,sources)['operator_review']['base']
    assert 'Контактов в работе: 10' in base['observation'] and 'неверном номере' not in base['observation']
    assert 'неверном номере' in base['contact_fact']['observation'] and base['contact_fact']['evidence']


def test_small_sample_and_no_contact_fact_is_clean():
    r=personal_report();r['attempts']=[];r['contact_stats']=phone_stats(dialed=3,reached=1,no_answer_only=2,automated_only=0,unknown=0,attempted_calls=3,repeat_attempts=0,reached_after_no_answer=0)
    packet,sources=advisor.evidence_packet(r)
    base=asyncio.run(advisor.advise_packet(packet,sources))['operator_review']['base']
    assert '33,3%' in base['observation'] and 'предварительный' in base['observation']
    assert base['contact_fact']['status']=='insufficient'


def test_refresh_existing_personal_brief_preserves_verified_fact_and_script():
    evidence={'call_id':42,'run_id':9,'phone':'9992223344','at':14,
              'quote':'Я покупаю в месяц на 5000 рублей коробки', 'base':'B1'}
    existing={'status':'ready','summary':'старый вывод','operator_review':{
       'script':{'status':'supported','observation':'Задала составной вопрос.', 'action':'Разделить вопросы.',
                 'test':'Проверить беседы.', 'evidence':[{'quote':'Размер и тираж?', 'step':'Параметры'}]},
       'base':{'status':'supported','observation':'Закупка — 5000 рублей в месяц.',
               'action':'Уточнить объём.', 'test':'Проверить карточку.', 'evidence':[evidence]}}}
    out=advisor.apply_contact_stats(existing,phone_stats())
    assert out['operator_review']['script']==existing['operator_review']['script']
    base=out['operator_review']['base']
    assert 'Контактов в работе: 10' in base['observation'] and 'неуспешные попытки' in base['observation']
    assert base['contact_fact']['observation']=='Закупка — 5000 рублей в месяц.'
    assert base['contact_fact']['evidence']==[evidence]
    assert existing['operator_review']['base']['evidence']==[evidence]
    assert advisor.apply_contact_stats(out,phone_stats())==out


def test_reach_includes_answered_machine_in_mango_denominator():
    s=phone_stats(dialed=219,reached=198,no_answer_only=21,automated_only=1,unknown=0,
                  attempted_calls=229,repeat_attempts=10,reached_after_no_answer=10)
    out=advisor._contact_stat_section(s)
    assert 'подтверждено: 198 (90,4%)' in out['observation']
    assert 'Из соединений только автоответчик — 1' in out['observation']
    assert 'не всегда означает подтверждённый разговор' in out['observation']


def test_actions_distinguish_failed_calls_repeat_benefit_and_zero_values():
    zero=advisor._contact_stat_section(phone_stats(dialed=10,reached=10,no_answer_only=0,automated_only=0,unknown=0,attempted_calls=10,repeat_attempts=0,reached_after_no_answer=0))
    assert 'неуспешн' not in zero['action'].lower()
    assert 'автоответчик' not in zero['observation'] and 'не установлен' not in zero['observation']
    unknown=advisor._contact_stat_section(phone_stats(dialed=10,reached=3,no_answer_only=3,automated_only=0,unknown=4,attempted_calls=10,repeat_attempts=0,reached_after_no_answer=0))
    assert 'Сначала проверить заполнение исходов' in unknown['action']
    assert 'повтор' not in unknown['action'].lower()
    case=advisor._contact_stat_section(phone_stats(dialed=219,reached=198,no_answer_only=21,automated_only=1,unknown=0,attempted_calls=229,repeat_attempts=10,reached_after_no_answer=10))
    assert 'вызовами: 21' in case['action'] and 'в другое время' in case['action']
    assert 'номерам: 10' in case['observation']


def test_largest_attributed_source_is_selected_and_personal_model_has_no_stats(monkeypatch):
    stats=phone_stats(by_base=[{'name':'Север','project':'Картон','dialed':5,'no_answer_only':2},
                                {'name':'Юг','project':'Картон','dialed':7,'no_answer_only':4}])
    section=advisor._contact_stat_section(stats)
    assert '«Юг»' in section['action'] and 'контактов 4' in section['action']
    monkeypatch.setenv('CVC_API_KEY','synthetic-test')
    transport=importlib.import_module('crm.callcenter_transport'); calls=[]
    packet,sources=advisor.evidence_packet(personal_report());packet['contact_stats']=stats
    ref=packet['operator_script_candidates'][0]['ref']
    async def fake(*args,**kwargs):
        calls.append(args[1]);return {'operator_review':{'script':{'status':'supported','observation':'Оператор назвал заявку.',
          'action':'Проверить источник.', 'test':'Проверить реплики.', 'refs':[ref]}, 'base':{'status':'insufficient'}}},0
    monkeypatch.setattr(transport,'request',fake)
    out=asyncio.run(advisor.advise_packet(packet,sources))
    assert out['operator_review']['base']['status']=='supported'
    assert 'contact_stats' not in calls[0] and 'by_base' not in calls[0] and 'Север' not in calls[0]
    assert packet['contact_stats']==stats


def test_hidden_source_does_not_appear_in_action():
    stats=phone_stats(by_base=[{'name':'B1_[скрыто]','project':'Картон','dialed':7,'no_answer_only':4},
                                {'name':'Юг','project':'Картон','dialed':5,'no_answer_only':2}])
    section=advisor._contact_stat_section(stats)
    assert '[скрыто]' not in section['action'] and 'источник' not in section['action'].lower()
    assert 'вызовами: 4' in section['action']
