"""Проверка шагов скрипта и качества прозвоненной базы по наблюдаемым признакам."""
from __future__ import annotations

import re
from collections import Counter, defaultdict


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
        ('Согласие на звонок эксперта', r'(?:можно|разрешите).{0,70}(?:эксперт|расч[её]т)|(?:эксперт|специалист).{0,50}(?:позвон|перезвон|созвон|встреч)'),
        ('Время звонка и куда прислать расчёт', r'(?:когда|во сколько).{0,30}удобно.{0,24}(?:созвон|позвон|звонок)|куда.{0,25}(?:прислать|направить|отправить).{0,20}расч[её]т'),
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
    'hh_ru': ('hh.ru', r'\b(?:ваканс\w*|найм\w*|нанима\w*|подбор\w* сотрудников|(?:привлека\w*|наход\w*|ищ\w*) сотрудников|hh\.?ru|хедхантер\w*)\b', (
        ('Выяснение потребности в найме', r'(?:ищете|нанимаете|подбираете|нуж\w*).{0,40}(?:сотрудник|новые люди)|есть ли.{0,24}ваканс|планируете.{0,24}(?:найм|наним|подбор)'),
        ('Проверка прежней регистрации', r'когда[- ]нибудь.{0,42}регистрир|(?:была|была ли|раньше|ранее|уже).{0,28}регистрац|регистрировал\w*.{0,30}(?:hh\.?ru|хедхантер)'),
        ('Проверка кабинета работодателя', r'(?:кабинет|аккаунт)\w*\s+работодател'),
        ('Интерес к регистрации или публикации', r'(?:рассматрива\w*|интересн\w*|планиру\w*|хотели ли).{0,56}(?:зарегистрир|регистрац|публик|размещ)|(?:зарегистрир|размест|опубликов).{0,48}(?:интересн|рассматрива)'),
    )),
    'packaging': ('Картонная упаковка', r'картон\w* (?:упаковк|короб)|гофрокартон|по поводу картона|(?:какие|ищете|искали) коробк', (
        ('Назначение упаковки', r'что (?:будете |хотите )?упаковыва|для чего.{0,24}(?:короб|упаков)|под какой товар|для маркетплейс'),
        ('Конструкция коробки', r'какая коробка|какие коробки|конструкци|четыр[её]хклапан|самосборн|крышк\w*[- ]дно|\bлоток\b'),
        ('Материал и цвет', r'гофрокартон|какой картон|какой цвет|(?:бел\w*|бур\w*).{0,18}картон|(?:тр[её]х|пяти)слойн|[35]\s*сло'),
        ('Печать', r'печать|флексограф|логотип|(?:один|два|[12])\s*цвет'),
        ('Размеры или образец', r'размер|габарит|длина.{0,40}ширина|есть ли образец'),
        ('Объём партии', r'тираж|сколько.{0,25}(?:штук|короб|единиц)|объ[её]м.{0,15}парти|количество короб'),
        ('Частота закупки', r'как часто|разов\w*|раз в месяц|регулярно.{0,23}(?:закуп|нужн)'),
        ('Сумма закупки в месяц', r'на какую сумму|месячн\w* бюджет|в месяц.{0,28}(?:закуп|трат)|закупа\w*.{0,28}в месяц'),
        ('Текущий поставщик', r'поставщик|перв\w* закупк|у кого.{0,20}(?:бер[её]те|закуп)'),
        ('Срок поставки', r'(?:когда|к какому сроку).{0,23}(?:партия|парт[июи]|нужн|поставк)|к какой дате|срок\w* доставк'),
        ('Город доставки', r'в какой город|куда.{0,20}(?:достав|отправ)|в каком город|город доставк'),
        ('Звонок менеджера и канал для расчёта', r'менеджер.{0,50}(?:позвон|свяж|созвон)|куда.{0,25}(?:прислать|направить|отправить).{0,20}расч[её]т'),
    )),
    'hangars': ('Строительство ангаров', r'\bангар\w*\b|завод металлоконструкций', (
        ('Актуальность строительства', r'строительств\w*.{0,30}(?:актуал|интерес|планиру)|ангар\w*.{0,24}актуал|актуал\w*.{0,24}ангар'),
        ('Место объекта', r'в каком город|в каком насел[её]нном|где.{0,28}(?:объект|строител|строить)|место строительств'),
        ('Назначение ангара', r'под что|для чего.{0,24}(?:ангар|нужен)|под какие задач|назначени'),
        ('Площадь или размеры', r'площад[ьи]|габарит|размер'),
        ('Металлокаркас', r'каркас|металлоконструкц'),
        ('Утепление', r'утепл|т[её]плый или холодный'),
        ('Срок начала строительства', r'когда.{0,36}(?:нач|строить|запуск)|срок\w*.{0,24}(?:начала|строительств)|готов\w*.{0,18}(?:начать|запуск)'),
        ('Наличие финансирования', r'финансирован|средства.{0,24}(?:предусмотр|выделен)|бюджет.{0,24}(?:выделен|предусмотр)'),
        ('Передача специалисту и время', r'специалист.{0,50}(?:уточнит|позвон|свяж)|(?:когда|во сколько).{0,18}удобно.{0,24}(?:принять звонок|созвон|позвон)'),
    )),
}

AUTOMATED = re.compile(r'оставьте (?:голосовое )?сообщение|говорите после сигнала|абонент недоступен|для соединения нажмите|голосов(?:ой помощник|ая почта)', re.I)
INTRO = re.compile(r'\b(?:меня зовут|это|вас беспокоит|я)\s+(?:ольга|оксана)\b|\b(?:ольга|оксана)\b.{0,32}\b(?:беспокоит|звоню|из компании)\b|\bзвоню (?:вам )?(?:по поводу|по вашей заявке|из компании)|производство картонной упаковки|мы созванивались с вами по поводу картона|мы.{0,22}(?:строим каркасные ангары|производим металлоконструкции)', re.I)
WRONG = re.compile(r'не туда попали|ошиблись номером|это не (?:его|е[её]|их) номер|не тот номер|вы ошиблись', re.I)
NO_REQUEST = re.compile(r'(?:не|никогда не) (?:оставлял[аи]?|подавал[аи]?|делал[аи]?) (?:никак\w* )?заявк|не (?:заказывал[аи]?|интересовал(?:ся|ась))\s+кухн|не искал\w*\s+коробк|коробк\w* (?:мы |я )?не искал', re.I)
NOT_RELEVANT = re.compile(r'не (?:актуально|интересно|ищем|нанимаем|планируем)|(?:нет|не открыто) ваканс|кухню уже (?:купил|заказал)|точно не (?:пл[её]нка|пвх)|бюджет\s+(?:меньше|до)\s+(?:250|двести пятьдесят)', re.I)
CALL_LATER = re.compile(r'(?:давайте|можете|пожалуйста)\s+перезвон\w*|сейчас (?:некогда|неудобно)|перезвоните (?:позже|вечером|завтра)', re.I)
DECLINE = re.compile(r'не звоните|не беспокойте|не нужно (?:мне |нам )?звонить|не надо (?:мне |нам )?звонить', re.I)
HH_REGISTERED = r'\bмы (?:уже |раньше )?регистрировались|у нас (?:уже )?есть (?:личный )?кабинет работодателя|мы зарегистрирован\w*|удалил\w*.{0,24}кабинет|аккаунт работодателя у нас есть'
HH_NO_HIRE = r'не (?:ищем|нанимаем|планируем.{0,14}найм)|нет ваканс\w*|найм не нужен'
ACCOUNTING_DECLINE = r'(?:бухгалтер\w*|уч[её]т).{0,28}(?:не нужен|не актуал|не интерес|устраивает)|не интересовал\w*.{0,28}бухгалтер|(?:есть|свой) бухгалтер.{0,18}(?:вс[её] устраивает|менять не будем)'
HIRE_PIVOT = r'как.{0,29}(?:привлека\w*|наход\w*|ищ\w*).{0,20}сотрудник|нужны ли.{0,18}(?:новые люди|сотрудник)|планируете.{0,18}найм|вопрос по вашей команде'
HH_HANDOFF = ('Согласие на передачу специалисту hh.ru', r'можно.{0,36}переда\w*.{0,38}(?:специалист|размещ|ваканс)|специалист.{0,44}(?:ваканс|регистрац|hh\.?ru)')
ACCOUNTING_OPEN = r'интересовал\w*.{0,32}бухгалтер|бухгалтер\w*.{0,32}(?:актуал|интерес)'
EARLY_NO = r'^\s*(?:нет\b|не интересовал\w*\b|не (?:нужно|актуально)\b)'
NEED_HIRE = r'\b(?:ищем|нанимаем|набираем)\s+(?:новых\s+)?(?:сотрудник\w*|людей|менеджер\w*)|(?:у нас )?есть ваканс\w*'
HH_NO_REG = r'не регистрировал\w*|регистрации не было|никогда не было регистрации'
HH_NO_CAB = r'(?:личного )?кабинета(?: работодателя)? нет|нет (?:личного )?кабинета работодателя'
HH_INTEREST = r'интересно.{0,24}(?:размест|опубликов|зарегистрир)|хотим.{0,24}(?:размест|опубликов|зарегистрир)'
FORBIDDEN_DIMENSION = re.compile(r'(?<!\d)60\s*[×xх*]\s*40\s*[×xх*]\s*40(?!\d)(?!\s*мм)|(?<!\d)600\s*[×xх*]\s*400\s*[×xх*]\s*400\s*мм\b', re.I)
SPEND = re.compile(r'\b(\d{1,3}(?:[ \u00a0]\d{3})+|\d+)\s*(тысяч\w*|тыс\b|рубл\w*|руб\b|₽)', re.I)
LEAD_CHECKS = {
    'packaging': (
        ('Неподходящий картон', r'(?<!не )\b(?:нужен|нужны|только|хотим).{0,28}(?:мелованн\w* картон|семислойный|7\s*слойн)'),
        ('Печать вне критериев', r'(?<!не )\b(?:нужна|нужен|хотим|только).{0,32}(?:полноцвет|сложн\w* макет|[3-9]\s*цвет|три цвета|четыре цвета)'),
    ),
    'hangars': (
        ('Тентовая или надувная конструкция', r'(?<!не )\b(?:нужен|хотим|только).{0,28}(?:тентов\w*|надувн\w*) (?:ангар|конструкц)'),
        ('Финансирование не подтверждено', r'финансирован\w*.{0,25}(?:не предусмотр|нет|ещ[её] реша)|(?:денег|средств) (?:пока )?нет|средства не выделен'),
        ('Строительство отложено', r'строить.{0,25}(?:не планиру|не собираем|не в этом году)|(?:сроков|планов) пока нет|(?:строительство|проект) на неопредел[её]нн'),
    ),
    'hh_ru': (('Регистрация или кабинет уже были', HH_REGISTERED),),
}


def _find(lines, pattern):
    return next((line for line in lines if re.search(pattern, line['text'], re.I)), None)


def _proof(label, line):
    return {'label': label, 'at': line['at'], 'quote': line['text'][:180]}


def _lead_evidence(script, client):
    evidence = []
    for label, pattern in LEAD_CHECKS.get(script, ()):
        reply = _find(client, pattern)
        if reply:
            evidence.append(_proof(label, reply))
    if script != 'packaging':
        return evidence
    size = _find(client, FORBIDDEN_DIMENSION.pattern)
    if size and not re.search(r'не (?:нужен|нужны|подходит)', size['text'], re.I):
        evidence.append(_proof('Размер вне критериев 60×40×40', size))
    for reply in client:
        if not (re.search(r'в месяц|ежемесячно', reply['text'], re.I)
                and re.search(r'закуп|тратим|расход|бюджет|коробк', reply['text'], re.I)):
            continue
        amounts = SPEND.findall(reply['text'])
        if len(amounts) != 1 or re.search(r'или|\bдо\b|от.{0,18}\bдо\b', reply['text'], re.I):
            continue
        number, unit = amounts[0]
        amount = int(re.sub(r'\s', '', number)) * (1000 if unit.casefold().startswith('тыс') else 1)
        if amount < 15000:
            evidence.append(_proof('Закупка меньше 15 000 ₽ в месяц', reply))
        elif amount < 30000:
            evidence.append(dict(_proof('Закупка 15 000–29 999 ₽, уточнить готовность к тесту', reply), follow_up=True))
        break
    return evidence


def _check_direction(key, caller, client, accounting_declined, combined):
    steps = SCRIPTS[key][2]
    lead = _lead_evidence(key, client)
    warnings = []
    price = _find(caller, r'(?:наша цена|наши услуги стоят|цена составит|мы сделаем за).{0,30}\d[\d ]*\s*(?:рубл|руб\b|тыс|₽)')
    if price and key != 'kitchen':
        warnings.append(_proof('Проверить названную оператором цену', price))
    if key == 'accounting' and combined:
        pivot = ('Переход к вопросу о сотрудниках', HIRE_PIVOT)
        steps = (pivot,) if accounting_declined else (*steps, pivot)
    elif key == 'hh_ru':
        affiliation = _find(caller, r'(?:я|мы).{0,14}(?:из компании hh\.?ru|сотрудник\w* hh\.?ru)|звоню из hh\.?ru')
        if affiliation:
            warnings.append(_proof('Проверить, как оператор представился', affiliation))
        if _find(client, HH_NO_HIRE) and not _find(caller, r'кабинет работодател|регистрир\w*.{0,28}hh\.?ru'):
            steps = steps[:1]
        if not lead and all(_find(client, pattern) for pattern in (NEED_HIRE, HH_NO_REG, HH_NO_CAB, HH_INTEREST)):
            steps = (*steps, HH_HANDOFF)
    elif key == 'hangars':
        amount = _find(caller, r'како[йе].{0,18}(?:бюджет|сумму)|сколько.{0,26}(?:денег|выделен|средств)')
        if amount:
            warnings.append(_proof('Проверить вопрос о конкретной сумме финансирования', amount))

    found, missing = [], []
    for label, rule in steps:
        match = _find(caller, rule)
        if match:
            found.append(_proof(label, match))
        else:
            missing.append(label)

    disqualified = any(not flag.get('follow_up') for flag in lead)
    handoff = _find(caller, {
        'packaging': r'(?:передам|переда[её]м).{0,30}менеджер|менеджер.{0,20}(?:вам )?позвонит',
        'hangars': r'(?:передам|переда[её]м).{0,30}специалист|специалист.{0,20}(?:вам )?позвонит',
        'hh_ru': HH_HANDOFF[1],
    }.get(key, r'(?!)'))
    if disqualified and handoff and key in ('packaging', 'hh_ru'):
        warnings.append(_proof('Проверить предложение передачи при неподходящих критериях', handoff))
    if (accounting_declined and key == 'accounting' and not combined) or (disqualified and not warnings):
        return 'stopped', found, [], lead, warnings
    if missing and _find(client, CALL_LATER.pattern) and not warnings:
        return 'stopped', found, [], lead, warnings
    return 'review' if missing or warnings else 'complete', found, missing, lead, warnings


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
    chosen = ([script] if script in matched else []) if script != 'auto' else matched
    if len(chosen) > 1 and set(chosen) != {'accounting', 'hh_ru'}:
        chosen = []
    result = {'script': chosen[0] if chosen else '', 'scripts': chosen, 'signals': []}
    explicit = {line['speaker'] for line in lines if re.match(r'^(оператор|менеджер)\b', str(line['speaker']), re.I)}
    roles = explicit or {line['speaker'] for line in lines if line['speaker'] and INTRO.search(line['text'])}
    if len(roles) != 1:
        return dict(result, state='unknown')
    operator = roles.pop()
    caller = [line for line in lines if line['speaker'] == operator]
    client = [line for line in lines if line['speaker'] and line['speaker'] != operator]
    if not client:
        return dict(result, state='unknown')
    accounting_declined = bool(_find(client, ACCOUNTING_DECLINE) or any(
        current['speaker'] != operator and previous['speaker'] == operator
        and re.search(ACCOUNTING_OPEN, previous['text'], re.I) and re.search(EARLY_NO, current['text'], re.I)
        for previous, current in zip(lines, lines[1:]) if current['speaker']))
    combined = script == 'auto' and 'accounting' in chosen
    signals = [label for label, rule in (('wrong_number', WRONG), ('no_request', NO_REQUEST))
               if _find(client, rule.pattern)]
    if not combined and (accounting_declined or _find(client, NOT_RELEVANT.pattern)):
        signals.append('not_relevant')
    result.update(live=True, signals=signals)
    if 'wrong_number' in signals or _find(client, DECLINE.pattern):
        return dict(result, state='stopped')
    if not chosen:
        return dict(result, state='other_topic')
    if not combined and 'not_relevant' in signals and chosen[0] in ('kitchen', 'accounting'):
        return dict(result, state='stopped')
    found, missing, lead, warnings, states = [], [], [], [], {}
    for key in chosen:
        state, proofs, gaps, flags, cautions = _check_direction(key, caller, client, accounting_declined, combined)
        states[key] = state
        prefix = SCRIPTS[key][0] + ' · ' if len(chosen) > 1 else ''
        found.extend(dict(proof, label=prefix + proof['label']) for proof in proofs)
        missing.extend(prefix + step for step in gaps)
        lead.extend(flags)
        warnings.extend(cautions)
    state = next(name for name in ('review', 'complete', 'stopped') if name in states.values())
    return dict(result, state=state, directions=states, found=found, missing=missing, lead=lead, warnings=warnings)


def summarize(rows, script: str, phone_key) -> dict:
    states, gaps = defaultdict(int), defaultdict(int)
    topics, warnings = Counter(), Counter()
    directions = defaultdict(Counter)
    contacts = defaultdict(list)
    for row in rows:
        check = row.get('script_check') or {}
        states[check.get('state', 'unknown')] += 1
        for key in check.get('scripts', [check.get('script')]):
            if key in SCRIPTS:
                topics[SCRIPTS[key][0]] += 1
        for key, state in check.get('directions', {}).items():
            directions[SCRIPTS[key][0]][state] += 1
        warnings.update({flag['label'] for flag in check.get('warnings', [])})
        for step in check.get('missing', []):
            gaps[step] += 1
        phone = phone_key(row.get('phone'))
        if phone:
            contacts[phone].append((check, row.get('duration_s') or 0))

    reached, not_reached, technical, repeats, talk_recorded = 0, 0, 0, 0, 0
    flags, criteria = Counter(), Counter()
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
        criteria.update({flag['label'] for check, _ in attempts for flag in check.get('lead', [])})
    return {
        'script': SCRIPTS[script][0] if script in SCRIPTS else 'По теме звонка',
        'states': dict(states), 'gaps': dict(gaps), 'topics': dict(topics),
        'directions': {key: dict(counters) for key, counters in directions.items()},
        'warnings': dict(warnings),
        'base': {'contacts': len(contacts), 'reached': reached, 'not_reached': not_reached,
                 'talk_recorded': talk_recorded,
                 'reach_pct': round(100 * reached / len(contacts), 1) if contacts else None,
                 'technical': technical, 'repeated_no_contact': repeats,
                 'wrong_number': flags['wrong_number'], 'no_request': flags['no_request'],
                 'not_relevant': flags['not_relevant'], 'criteria': dict(criteria)},
    }
