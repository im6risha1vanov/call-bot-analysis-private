from crm import callcenter_quality as q
from crm import callcenter_verdict as v


def sample():
    return [dict(speaker='Оператор', at='00:01', text='Меня зовут Ольга, нужны коробки?'),
            dict(speaker='Клиент', at='00:05', text='Да, нужны коробки.'),
            dict(speaker='Клиент', at='00:09', text='Да, пусть менеджер позвонит.')]


def payload():
    return {'decisions': [{'project':'packaging', 'category':'qualified', 'reason':'потребность',
                           'evidence':[{'at':'00:05','quote':'нужны коробки'}], 'next_step':True,
                           'next_step_text':'звонок менеджера',
                           'next_step_evidence':{'at':'00:09','quote':'пусть менеджер позвонит'}}]}


def test_quotes_must_match_speaker_and_time():
    lines = sample(); check = q.assess(lines, 'packaging', 30, False)
    assert v.validate(payload(), lines, check)['decisions'][0]['category'] == 'pending'
    bad = payload(); bad['decisions'][0]['evidence'][0]['at'] = '00:01'
    assert v.validate(bad, lines, check)['decisions'][0]['category'] == 'pending'
    bad = payload(); bad['decisions'][0]['next_step_evidence']['quote'] = 'да, согласен'
    assert v.validate(bad, lines, check)['decisions'][0]['category'] == 'pending'


def test_stop_and_unattributed_calls():
    lines = sample() + [dict(speaker='Клиент', at='00:12', text='Больше не звоните')]
    check = q.assess(lines, 'packaging', 30, False)
    assert check['stop_contact']
    decision = v.validate(payload(), lines, check)['decisions'][0]
    assert decision['category'] == 'pending' and decision['next_step'] is False
    assert v.validate(payload(), [dict(speaker='', at='00:05', text='нужны коробки')], check) == check


def test_friction_and_coaching_require_appropriate_speaker():
    lines = sample(); check = q.assess(lines, 'packaging', 30, False)
    data = {'friction':[{'project':'packaging','step':'вопрос','issue':'сомнение','proposal':'уточнить','at':'00:01','quote':'Меня зовут Ольга'}],
            'coaching':[{'step':'приветствие','advice':'уточнить','at':'00:05','quote':'нужны коробки'}]}
    actual = v.validate(data, lines, check)
    assert actual['friction'] == [] and actual['coaching'] == []


def test_negative_eligibility_always_overrides_model():
    for client in ('Вы ошиблись номером', 'Больше не звоните'):
        lines = sample() + [dict(speaker='Клиент', at='00:12', text=client)]
        check = q.assess(lines, 'packaging', 30, False)
        for category in ('qualified', 'reusable', 'competitor'):
            data = payload(); data['decisions'][0]['category'] = category
            assert v.validate(data, lines, check)['decisions'][0]['category'] == 'pending'
    check = q.assess(sample(), 'packaging', 30, False)
    check['state'] = 'answering_machine'
    assert v.validate(payload(), sample(), check)['decisions'][0]['category'] == 'pending'


def test_disqualifier_overrides_qualified_model():
    lines = sample() + [dict(speaker='Клиент', at='00:12', text='Нужен мелованный картон')]
    check = q.assess(lines, 'packaging', 30, False)
    assert v.validate(payload(), lines, check)['decisions'][0]['category'] != 'qualified'


def test_offsets_are_canonical_but_not_invented():
    lines = sample(); lines[1]['at'] = 5; lines[2]['at'] = 9
    check = q.assess(lines, 'packaging', 30, False)
    assert v.validate(payload(), lines, check)['decisions'][0]['category'] == 'pending'
    lines[1]['at'] = None
    check = q.assess(lines, 'packaging', 30, False)
    assert v.validate(payload(), lines, check)['decisions'][0]['category'] == 'pending'
    data = payload(); data['decisions'][0]['evidence'][0]['at'] = None
    assert v.validate(data, lines, check)['decisions'][0]['category'] == 'pending'


def test_friction_stable_codes_and_measure():
    lines = sample(); check = q.assess(lines, 'packaging', 30, False)
    friction = {'project':'packaging', 'step':'Что-то', 'stage_id':'need', 'issue_code':'price',
                'issue':'Спрашивает цену', 'proposal':'Объяснить расчет', 'measure':'Доля согласий на расчет',
                'at':'00:05', 'quote':'нужны коробки'}
    result = v.validate({'friction':[friction]}, lines, check)['friction'][0]
    assert (result['step'], result['stage_id'], result['issue_code'], result['measure']) == (
        'Выяснение потребности', 'need', 'price', 'Доля согласий на расчет')
    friction['issue_code'] = 'unrecognised'
    assert not v.validate({'friction':[friction]}, lines, check)['friction']


def test_local_fallback_competitor_disqualified_and_safe_unknown():
    def call(text):
        return [dict(speaker='Оператор', at=0, text='Меня зовут Ольга, нужны коробки?'),
                dict(speaker='Клиент', at=5, text=text)]
    assert q.assess(call('Мы производим коробки сами'), 'packaging', 30, False)['decisions'][0]['category'] == 'pending'
    assert q.assess(call('Мы продаем коробки клиентам'), 'packaging', 30, False)['decisions'][0]['category'] == 'competitor'
    assert q.assess(call('Мы не производим коробки'), 'packaging', 30, False)['decisions'][0]['category'] == 'pending'
    lines = call('Нам нужны коробки, нужен мелованный картон')
    assert q.assess(lines, 'packaging', 30, False)['decisions'][0]['category'] == 'reusable'
    lines.append(dict(speaker='Клиент', at=9, text='Не звоните больше'))
    assert q.assess(lines, 'packaging', 30, False)['decisions'][0]['category'] == 'pending'


def test_packaging_requires_evidence_for_every_fact_and_separate_consent():
    texts = ['Нам нужны коробки', 'Нужен гофрокартон 3 слоя', 'Цвет белый картон',
             'Без печати', 'Размер 50×40×40 см', 'Закупаем коробки за 30 тысяч в месяц',
             'Да, пусть менеджер позвонит']
    lines = [dict(speaker='Оператор', at=0, text='Меня зовут Ольга, нужны коробки?')]
    lines += [dict(speaker='Клиент', at=i+1, text=x) for i, x in enumerate(texts)]
    facts = ['need', 'material', 'color', 'printing', 'size', 'spend']
    evidence = [dict(at=i+1, quote=texts[i], fact=fact) for i, fact in enumerate(facts)]
    data = {'decisions':[dict(project='packaging', category='qualified', reason='Подходящие критерии',
                              evidence=evidence, next_step=True, next_step_text='Звонок менеджера',
                              next_step_evidence=dict(at=7, quote=texts[6]))]}
    check = q.assess(lines, 'packaging', 30, False)
    decision = v.validate(data, lines, check)['decisions'][0]
    assert decision['category'] == 'qualified' and decision['product_qualified']
    data['decisions'][0]['next_step'] = False
    decision = v.validate(data, lines, check)['decisions'][0]
    assert decision['category'] == 'pending' and decision['product_qualified'] and not decision['next_step']
    data['decisions'][0]['evidence'] = evidence[:-1]
    assert not v.validate(data, lines, check)['decisions'][0]['product_qualified']
    data['decisions'][0]['evidence'] = [dict(at=7, quote=texts[6], fact=x) for x in facts]
    assert not v.validate(data, lines, check)['decisions'][0]['product_qualified']
    data['decisions'][0]['evidence'] = evidence
    lines[1]['text'] = 'Нам не нужны коробки'
    data['decisions'][0]['evidence'][0]['quote'] = lines[1]['text']
    assert not v.validate(data, lines, check)['decisions'][0]['product_qualified']
