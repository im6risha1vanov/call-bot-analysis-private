"""Проверка шагов скрипта и качества прозвоненной базы по наблюдаемым признакам."""
from __future__ import annotations

import re
from collections import defaultdict


SCRIPTS = {
    'accounting': ('Бухгалтерское сопровождение',
                   r'бухгалтер|\bналогообложен|\bусн\b|\bосно\b', (
        ('Цель разговора и согласие по времени', r'(?:займ\w*|буквальн\w*)\s+(?:буквально\s+)?(?:пару|две|[23])\s+минут|удобно.*(?:говорить|разговаривать)'),
        ('Форма бизнеса', r'\bооо\b|\bип\b|юридическ\w* форм|индивидуальн\w* предпринимател'),
        ('Ниша и операции', r'чем (?:вы )?занимаетесь|маркетплейс|импорт|выда[её]те чек|касс\w*'),
        ('Налоговый режим', r'систем\w* налогообложен|налогов\w* режим|\bусн\b|\bосно\b|\bпатент\b|упрощ[её]нк'),
        ('Число сотрудников', r'(?:сколько|число|количество).{0,30}(?:сотрудник|работник|человек)|сотрудник\w* (?:у вас )?есть'),
        ('Выручка или оборот', r'\b(?:выручк\w*|оборот\w*)\b'),
        ('Кто сейчас ведёт учёт', r'кто.{0,20}(?:вед[её]т|занимается).{0,24}(?:бухгалтер|уч[её]т)|штатн\w* бухгалтер|аутсорс|вед[её]те (?:сами|самостоятельно)'),
        ('Причина смены бухгалтера', r'что (?:вас )?подтолкнул|почему.{0,32}(?:смен|ищ|переход)|что.{0,20}не устраивает'),
        ('Ожидания по цене', r'на какую.{0,35}(?:стоимость|сумму)|какой.{0,15}бюджет|рассчитываете.{0,20}(?:рубл|тысяч)'),
        ('Срок перехода', r'когда.{0,28}(?:планиру|переход|начать)|срок\w*.{0,28}(?:переход|смен)|с какого месяца'),
        ('Звонок эксперта и время', r'(?:эксперт|специалист).{0,50}(?:позвон|перезвон|созвон|встреч)|(?:когда|во сколько).{0,24}удобно.{0,24}(?:созвон|позвон)'),
    )),
    'kitchen': ('Кухонные гарнитуры', r'\b(?:кухн\w*|гарнитур\w*)\b', (
        ('Срок покупки', r'ближайш\w*.*месяц|в какие сроки|когда.*планиру|срок\w*.*(?:покуп|установ)'),
        ('Город или регион установки', r'в каком город|какой город|где.*установ|куда.*достав|какой регион'),
        ('Выгода для клиента', r'\b(?:скидк\w*|бесплатн\w*|акци[яию]|гаранти\w*)\b'),
        ('Материал и плёнка ПВХ', r'пл[её]нк|\bпвх\b|покрыти|эмаль|материал.*фасад'),
        ('Бюджет от 250 тыс. ₽', r'(?:250|двести пятьдесят)(?:\s*тыс|\s*000)'),
        ('Имя клиента', r'как (?:к вам обращ|вас зовут|ваше имя)|имя и отчество'),
        ('Время звонка специалиста', r'(?:когда|в какое время|во сколько).*удобн|удобн\w*.*(?:позвон|созвон)'),
    )),
    'hh_ru': ('hh.ru', r'\b(?:ваканс\w*|найм\w*|нанима\w*|подбор\w* сотрудников|hh\.?ru|хедхантер\w*)\b', (
        ('Выяснение потребности в найме', r'(?:ищете|нанимаете|подбираете|нуж\w*).*сотрудник|есть ли.*ваканс|планируете.*(?:найм|наним|подбор)'),
        ('Проверка регистрации', r'зарегистрир|регистрац'),
        ('Проверка кабинета работодателя', r'кабинет\w*\s+работодател|личн\w* кабинет|аккаунт\w*\s+работодател|(?:есть ли|создавали|открывали).{0,30}\bкабинет'),
        ('Интерес к регистрации или публикации', r'(?:рассматрива\w*|интересн\w*|планиру\w*).{0,56}(?:регистрац|публик|размещ|hh\.ru)|(?:зарегистрир|размест|опубликов).{0,48}(?:интересн|рассматрива)'),
        ('Предложение звонка специалиста', r'(?:специалист|коллега).*позвон|передам.*специалист|можно.*(?:передать|перезвонить)'),
    )),
    'packaging': ('Картонная упаковка', r'картон\w* (?:упаковк|короб)|гофрокартон|по поводу картона|(?:какие|ищете|искали) коробк', ()),
}

AUTOMATED = re.compile(r'оставьте (?:голосовое )?сообщение|говорите после сигнала|абонент недоступен|для соединения нажмите|голосов(?:ой помощник|ая почта)', re.I)
INTRO = re.compile(r'\b(?:меня зовут|это|вас беспокоит|я)\s+(?:ольга|оксана)\b|\b(?:ольга|оксана)\b.{0,32}\b(?:беспокоит|звоню|из компании)\b|\bзвоню (?:вам )?(?:по поводу|по вашей заявке|из компании)|производство картонной упаковки|мы созванивались с вами по поводу картона', re.I)
WRONG = re.compile(r'не туда попали|ошиблись номером|это не (?:его|е[её]|их) номер|не тот номер|вы ошиблись', re.I)
NO_REQUEST = re.compile(r'(?:не|никогда не) (?:оставлял[аи]?|подавал[аи]?|делал[аи]?) (?:никак\w* )?заявк|не (?:заказывал[аи]?|интересовал(?:ся|ась))\s+кухн', re.I)
NOT_RELEVANT = re.compile(r'не (?:актуально|интересно|ищем|нанимаем|планируем)|(?:нет|не открыто) ваканс|кухню уже (?:купил|заказал)|точно не (?:пл[её]нка|пвх)|бюджет\s+(?:меньше|до)\s+(?:250|двести пятьдесят)', re.I)
CALL_LATER = re.compile(r'(?:давайте|можете|пожалуйста)\s+перезвон\w*|сейчас (?:некогда|неудобно)|перезвоните (?:позже|вечером|завтра)', re.I)
DECLINE = re.compile(r'не звоните|не беспокойте|не нужно (?:мне |нам )?звонить|не надо (?:мне |нам )?звонить', re.I)
HH_REGISTERED = re.compile(r'\bмы (?:уже )?регистрировались|у нас (?:уже )?есть (?:личный )?кабинет|мы зарегистрирован\w*|аккаунт работодателя у нас есть', re.I)


def assess(lines: list[dict], script: str, duration: int, low_confidence: bool) -> dict:
    if not lines:
        return {'state': 'unknown' if duration else 'not_reached', 'signals': []}
    if low_confidence:
        return {'state': 'unknown', 'signals': []}
    first = lines[0]
    if AUTOMATED.search(first['text']):
        return {'state': 'answering_machine', 'signals': []}
    matched = [key for key, (_, topic, _) in SCRIPTS.items()
               if any(re.search(topic, line['text'], re.I) for line in lines)]
    if script == 'auto':
        script = matched[0] if len(matched) == 1 else ''
    result = {'script': script if script in matched else '', 'signals': []}
    explicit = {line['speaker'] for line in lines if re.match(r'^(оператор|менеджер)\b', str(line['speaker']), re.I)}
    roles = explicit or {line['speaker'] for line in lines if line['speaker'] and INTRO.search(line['text'])}
    if len(roles) != 1:
        return dict(result, state='unknown')
    operator = roles.pop()
    caller = [line for line in lines if line['speaker'] == operator]
    client = [line for line in lines if line['speaker'] and line['speaker'] != operator]
    if not client:
        return dict(result, state='unknown')
    signals = [label for label, rule in (('wrong_number', WRONG), ('no_request', NO_REQUEST),
                                        ('not_relevant', NOT_RELEVANT))
               if any(rule.search(line['text']) for line in client)]
    result.update(live=True, signals=signals)
    if signals or any(DECLINE.search(line['text']) or
                      (script == 'hh_ru' and HH_REGISTERED.search(line['text'])) for line in client):
        return dict(result, state='stopped')
    if not result['script']:
        return dict(result, state='other_topic')
    steps = SCRIPTS[script][2]
    if not steps:
        return dict(result, state='needs_script')
    found, missing = [], []
    for label, rule in steps:
        evidence = next((line for line in caller if re.search(rule, line['text'], re.I)), None)
        if evidence is None:
            missing.append(label)
        else:
            found.append({'label': label, 'at': evidence['at'], 'quote': evidence['text'][:180]})
    if missing and any(CALL_LATER.search(line['text']) for line in client):
        return dict(result, state='stopped')
    return dict(result, state='review' if missing else 'complete', found=found, missing=missing)


def summarize(rows, script: str, phone_key) -> dict:
    states, gaps = defaultdict(int), defaultdict(int)
    topics = defaultdict(int)
    contacts = defaultdict(list)
    for row in rows:
        check = row.get('script_check') or {}
        states[check.get('state', 'unknown')] += 1
        if check.get('script') in SCRIPTS:
            topics[SCRIPTS[check['script']][0]] += 1
        for step in check.get('missing', []):
            gaps[step] += 1
        phone = phone_key(row.get('phone'))
        if phone:
            contacts[phone].append((check, row.get('duration_s') or 0))

    reached, not_reached, technical, repeats, talk_recorded = 0, 0, 0, 0, 0
    flags = defaultdict(int)
    for attempts in contacts.values():
        talk_recorded += any(seconds > 0 for _, seconds in attempts)
        if any(check.get('live') for check, _ in attempts):
            reached += 1
        elif all(not seconds or check.get('state') == 'answering_machine' for check, seconds in attempts):
            not_reached += 1
            repeats += len(attempts) >= 2
        else:
            technical += 1
        for flag in {flag for check, _ in attempts for flag in check.get('signals', [])}:
            flags[flag] += 1
    return {
        'script': SCRIPTS[script][0] if script in SCRIPTS else 'По теме звонка',
        'states': dict(states), 'gaps': dict(gaps), 'topics': dict(topics),
        'packaging_pending': sum(1 for row in rows if (row.get('script_check') or {}).get('script') == 'packaging'),
        'base': {'contacts': len(contacts), 'reached': reached, 'not_reached': not_reached,
                 'talk_recorded': talk_recorded,
                 'reach_pct': round(100 * reached / len(contacts), 1) if contacts else None,
                 'technical': technical, 'repeated_no_contact': repeats,
                 'wrong_number': flags['wrong_number'], 'no_request': flags['no_request'],
                 'not_relevant': flags['not_relevant']},
    }
