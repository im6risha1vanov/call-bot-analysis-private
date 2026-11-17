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
    assert report['missing'] == ['Проверка прежней регистрации', 'Интерес к регистрации или публикации']
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
    assert bad_number['state'] == 'stopped' and bad_number['signals'] == ['wrong_number']
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


def test_auto_classifies_packaging_without_applying_accounting_steps():
    text = ('Клиент: Добрый день.\nОператор: Это Ольга, производство картонной упаковки. '
            'Подготовим для вас расчёт. Какие коробки нужны?\nКлиент: Самосборные.')
    check = assess(split_lines(text), 'auto', 40, False)
    assert check['script'] == 'packaging' and check['state'] == 'review'
    assert check['live'] and 'Размеры или образец' in check['missing'] and 'Форма бизнеса' not in check['missing']
    assert summarize([{'script_check': check}], 'auto', _phone_key)['topics'] == {'Картонная упаковка': 1}


def test_accounting_decline_does_not_stop_hh_branch_or_double_count_calls():
    text = ('Оператор: Вы интересовались бухгалтерскими услугами?\n'
            'Клиент: Нет, не интересовались.\n'
            'Оператор: Как сейчас привлекаете сотрудников? Ищете сотрудников? Компания когда-нибудь регистрировалась на hh.ru?\n'
            'Клиент: Ищем менеджеров. Мы раньше регистрировались.\n'
            'Оператор: Подскажите, есть ли кабинет работодателя?\nКлиент: Кабинет уже удалили.')
    result = assess(split_lines(text), 'auto', 54, False)
    assert result['directions'] == {'accounting': 'complete', 'hh_ru': 'stopped'}
    assert 'not_relevant' not in result['signals']
    assert result['lead'][0]['label'] == 'Регистрация или кабинет уже были'
    summary = summarize([{'phone': '79001230001', 'script_check': result}], 'auto', _phone_key)
    assert summary['states'] == {'complete': 1}
    assert len(summary['topics']) == 2
    assert summary['base']['contacts'] == 1


def test_packaging_lower_bound_and_trial_band():
    prefix = 'Оператор: Это Ольга, производство картонной упаковки.\nКлиент: '
    def flags(amount):
        return assess(split_lines(prefix + f'На закупку коробок в месяц тратим {amount} рублей.'), 'auto', 45, False)['lead']

    assert flags('14 999')[0]['label'] == 'Закупка меньше 15 000 ₽ в месяц'
    assert flags('15 000')[0]['follow_up']
    assert flags('30 000') == []
    assert flags('от 15 000 до 25 000') == []
    size = assess(split_lines(prefix + 'Нужны коробки 60×40×40 см.'), 'auto', 45, False)
    assert size['state'] == 'stopped' and size['lead'][0]['label'] == 'Размер вне критериев 60×40×40'
    assert not assess(split_lines(prefix + 'Мелованный картон нам не нужен.'), 'auto', 45, False)['lead']


def test_hangars_financing_is_separate_from_prohibited_question_about_amount():
    text = ('Оператор: Строительство ангара сейчас актуально? Какой у вас бюджет?\n'
            'Клиент: Ангар нужен, но финансирование ещё решается.')
    result = assess(split_lines(text), 'auto', 54, False)
    assert result['script'] == 'hangars'
    assert result['warnings'][0]['label'] == 'Проверить вопрос о конкретной сумме финансирования'
    assert result['lead'][0]['label'] == 'Финансирование не подтверждено'


def test_sheet_attribution_uses_dated_attempts_and_never_guesses_between_projects():
    from crm.callcenter_base import report

    day = '2026-11-17'
    def lead(phone, dated=False):
        return {'phone': phone, 'attempts': [{'result': 'НДЗ', 'date': day}] if dated else []}

    book = {'Упаковка': [lead('79001230001', True), lead('79001230002'), lead('123')],
            'Ангары': [lead('89001230001'), lead('79001230002')]}
    calls = [{'phone': f'7900123000{i}', 'duration_s': 0, 'script_check': None} for i in (1, 1, 2, 3)]
    summary = report(book, calls, day, 'now')
    assert (summary['matched'], summary['ambiguous'], summary['absent']) == (1, 1, 1)
    assert (summary['sheet_contacts'], summary['duplicate_rows'], summary['invalid_rows']) == (2, 2, 1)
    assert summary['projects'][0]['no_contact'] == 1
