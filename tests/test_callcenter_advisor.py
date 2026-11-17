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
    analysis=importlib.import_module('analysis'); calls=[]
    async def fake(*args, **kwargs):
        calls.append((args, kwargs)); return response(), 0
    monkeypatch.setattr(analysis, '_call', fake)
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
    analysis=importlib.import_module('analysis'); calls=[]
    async def fake(*args, **kwargs):
        calls.append((args, kwargs)); return response(), 0
    monkeypatch.setattr(analysis, '_call', fake)
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
    analysis=importlib.import_module('analysis'); calls=[]
    async def fake(*args,**kwargs):
        calls.append(args[1]); return (response('E999') if len(calls)==1 else response()), 0
    monkeypatch.setattr(analysis,'_call',fake)
    packet,sources=advisor.evidence_packet(report())
    assert asyncio.run(advisor.advise_packet(packet,sources))['status']=='ready'
    assert len(calls)==2 and 'Исправь прошлый ответ' in calls[1]
    async def fail(*args,**kwargs): return response('E999'),0
    monkeypatch.setattr(analysis,'_call',fail)
    with pytest.raises(advisor.SynthesisError): asyncio.run(advisor.advise_packet(packet,sources))
