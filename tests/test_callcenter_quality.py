from crm.call_analysis import _phone_key, split_lines
from crm.callcenter_quality import assess, summarize


def test_operator_can_be_second_speaker_and_step_quotes_come_from_operator():
    text = '\n'.join([
        '[00:00 Спикер 0] Алло. Я зарегистрирован раньше.',
        '[00:02 Спикер 1] Меня зовут Оксана. Вы ищете сотрудника?',
        '[00:05 Спикер 0] Да, вакансия есть.',
        '[00:12 Спикер 1] Есть ли кабинет работодателя? Специалист вам позвонит.',
    ])
    report = assess(split_lines(text), 'hh_ru', 35, False)
    assert report['state'] == 'review'
    assert report['missing'] == ['Проверка регистрации', 'Интерес к регистрации или публикации']
    assert {step['at'] for step in report['found']} == {2, 12}


def test_unavailable_and_wrong_script_are_separate_from_missed_steps():
    lines = split_lines('Оператор: Хотите кухню?\nКлиент: Думаю.')
    assert assess(lines, 'hh_ru', 25, False)['state'] == 'other_topic'
    assert assess(lines, 'kitchen', 25, True)['state'] == 'unknown'
    assert assess([], 'hh_ru', 40, False)['state'] == 'unknown'
    assert assess([], 'hh_ru', 0, False)['state'] == 'not_reached'
    voicemail = split_lines('[00:01 Спикер 0] Оставьте сообщение после сигнала.')
    assert assess(voicemail, 'kitchen', 21, False)['state'] == 'answering_machine'


def test_accounting_uses_the_business_checklist():
    text = ('Клиент: Слушаю.\nОператор: Меня зовут Ольга, бухгалтерское сопровождение. '
            'Займу пару минут. У вас ООО или ИП? Какая система налогообложения? Кто ведёт бухгалтерию?\n'
            'Клиент: ИП. Делаю сам.')
    report = assess(split_lines(text), 'accounting', 43, False)
    assert report['state'] == 'review'
    assert {step['label'] for step in report['found']} == {
        'Цель разговора и согласие по времени', 'Форма бизнеса', 'Налоговый режим', 'Кто сейчас ведёт учёт',
    }


def test_quality_counts_contacts_instead_of_calls_and_preserves_unknown():
    bad_number = assess(split_lines('Оператор: Меня зовут Ольга.\nКлиент: Вы не туда попали.'), 'kitchen', 19, False)
    assert bad_number == {'state': 'stopped', 'signals': ['wrong_number']}
    rows = [
        {'phone': '+7 900 100 00 01', 'duration_s': 19, 'script_check': bad_number},
        {'phone': '89001000001', 'duration_s': 0, 'script_check': {'state': 'not_reached'}},
        {'phone': '+7 900 100 00 02', 'duration_s': 0, 'script_check': {'state': 'not_reached'}},
        {'phone': '89001000002', 'duration_s': 10, 'script_check': {'state': 'answering_machine'}},
        {'phone': '+7 900 100 00 03', 'duration_s': 30, 'script_check': {'state': 'unknown'}},
    ]
    base = summarize(rows, 'kitchen', _phone_key)['base']
    assert (base['contacts'], base['reached'], base['not_reached'], base['technical']) == (3, 1, 1, 1)
    assert base['repeated_no_contact'] == base['wrong_number'] == 1
    assert base['reach_pct'] == 33.3
