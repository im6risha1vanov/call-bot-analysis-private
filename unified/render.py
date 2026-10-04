"""One compact, safe report and a cached overview of the exact saved run."""
from __future__ import annotations

import html
import hashlib
import re
from datetime import datetime
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from methodology.evaluation import evidence_valid

FORMAT_VERSION = 'unified_20261004_v1'
TITLES = {
    'implication_questions': 'Уточнить последствия проблемы',
    'problem_over_situational': 'Раскрыть затруднения клиента',
    'explicit_need': 'Уточнить, какой результат нужен клиенту',
    'decision_influence': 'Выяснить, кто участвует в решении',
    'brush_off_handled': 'Разобраться в причине отговорки',
    'sold_meeting': 'Связать следующий разговор с пользой для клиента',
    'concrete_commitment': 'Согласовать конкретное продолжение',
    'call_reason': 'Объяснить деловой повод звонка',
    'no_early_pitch': 'Связать презентацию с потребностью',
    'insight': 'Добавить полезное наблюдение о задаче клиента',
    'talk_share': 'Дать клиенту возможность ответить',
    'gatekeeper_passed': 'Получить нужного участника разговора',
}


def units(text):
    return len(text.encode('utf-16-le')) // 2


def clean(value, limit=300, *, expand=True):
    if not isinstance(value, str):
        return ''
    text = ' '.join(value.split())
    # Never stringify an object/model response into user-facing prose.
    if text.startswith(('{', '[', '```')) or re.search(r'"\w+"\s*:', text):
        return ''
    if expand:
        text = re.sub(r'\bКП\b', 'коммерческое предложение', text)
        text = re.sub(r'\bЛПР\b', 'человек, принимающий решение', text)
        text = re.sub(r'\bГЦК\b', 'генерация целевых контактов', text)
        text = re.sub(r'\bОПС\b', 'определение посетителей сайта', text)
        text = re.sub(r'\bКЦ\b', 'колл-центр', text)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def mask_phone(number):
    digits = ''.join(c for c in str(number or '') if c.isdigit())
    return '+*** *** ' + digits[-4:] if len(digits) >= 4 else 'номер не указан'


def phone_fingerprint(number):
    digits = ''.join(c for c in str(number or '') if c.isdigit())
    if len(digits) == 11 and digits[0] == '8':
        digits = '7' + digits[1:]
    return hashlib.sha256(digits.encode()).hexdigest() if len(digits) >= 7 else None


def redact_phone(text, row):
    digest = (row.get('configuration') or {}).get('phone_sha256') or phone_fingerprint(row['metadata'].get('phone'))
    if not digest:
        return text
    return re.sub(r'(?<!\d)\+?\d(?:[ ()\-]*\d){6,14}(?!\d)',
                  lambda match: '[номер скрыт]' if phone_fingerprint(match.group()) == digest else match.group(), text)


def quote(value, transcript):
    """Only literal source text; explanatory evidence is not a fabricated quote."""
    if not isinstance(value, str):
        return None
    candidates = re.findall(r'«([^»]+)»|"([^"\n]+)"', value)
    candidates = [a or b for a, b in candidates] + [value]
    for candidate in candidates:
        if candidate.strip() and evidence_valid(candidate.strip(), transcript):
            return candidate.strip()
    return None


def observation_text(value, actual):
    text = clean(value, expand=False)
    for quoted in ('«' + actual + '»', '"' + actual + '"'):
        text = text.replace(quoted, '')
    return clean(text.strip(' —–-:;'))


def header(row):
    meta = row['metadata']
    seconds = max(0, int(meta.get('duration_seconds') or 0))
    stamp = meta.get('call_started_at')
    try:
        date = datetime.fromisoformat(stamp).strftime('%d.%m.%Y %H:%M') if stamp else 'дата не указана'
    except (TypeError, ValueError):
        date = 'дата не указана'
    return (f"📞 Разбор звонка №{row['call_id']}\n"
            f"{clean(meta.get('manager'), 75) or 'Менеджер не указан'} · {date} · {seconds // 60}:{seconds % 60:02d}\n"
            f"Клиент: {mask_phone(meta.get('phone'))}")


def observations(row):
    primary = row['primary_analysis']; analysis = primary.get('analysis') or {}
    aux = row.get('auxiliary') or {}; combined = aux.get('combined') or {}
    readable = combined.get('readable_report') or {}
    transcript = row['transcript']
    good, weak = [], []
    for name, target in (('strengths', good), ('moments', weak)):
        for item in readable.get(name) or []:
            actual = quote(item.get('quote'), transcript)
            if actual:
                target.append({'key': item.get('criterion'), 'text': clean(item.get('what')),
                               'quote': actual, 'suggestion': clean(item.get('say_instead'))})
    miss = readable.get('main_miss') or {}
    if miss and not any(item['key'] == miss.get('criterion') for item in weak):
        actual = quote(miss.get('quote'), transcript)
        if actual:
            weak.append({'key': miss.get('criterion'), 'text': clean(miss.get('explanation')),
                         'quote': actual, 'suggestion': ''})
    # The active scale stays active. Additional observations cannot erase a
    # primary failure or turn different scales into a shared numeric result.
    primary_rows = analysis.get('rows') or []
    conflicts = []
    stage = (combined.get('classification') or {}).get('stage')
    aliases = {'concrete_commitment': 'next_step', 'call_reason': 'clarity',
               'gatekeeper_passed': 'honest_transfer'}
    for native, target, passed in ((primary_rows, good, True), (primary_rows, weak, False)):
        for item in native:
            if not item.get('applicable', True) or bool(item.get('passed')) != passed:
                continue
            actual = quote(item.get('evidence'), transcript)
            if not actual:
                continue
            key = item.get('key')
            if stage in {'presentation', 'demo_preparation', 'demo_results', 'support'} and key in {'sold_meeting', 'problem_over_situational', 'insight'} and not passed:
                conflicts.append(key)
                continue
            if any(p['key'] == key for p in target):
                continue
            opposite = weak if passed else good
            if any(p['key'] in {key, aliases.get(key, key)} for p in opposite):
                conflicts.append(key)
                opposite[:] = [p for p in opposite if p['key'] not in {key, aliases.get(key, key)}]
                continue
            target.append({'key': key, 'text': TITLES.get(key) or clean(item.get('title')),
                           'quote': actual, 'suggestion': ''})
    # Keep only source-backed legacy short observations; ignore invented times.
    brief = primary.get('short_report') or {}
    for name, target in (('good', good), ('weak', weak)):
        for item in brief.get(name) or []:
            actual = quote(item.get('text'), transcript)
            if actual and not any(p['quote'] == actual for p in good + weak):
                target.append({'key': None, 'text': observation_text(item.get('text'), actual), 'quote': actual,
                               'suggestion': clean(item.get('should_say'))})
    # Avoid counting the same quote or the same criterion twice.
    def unique(items):
        out = []; keys = set(); quotations = set()
        for item in items:
            if item['quote'] in quotations or item['key'] and item['key'] in keys:
                continue
            out.append(item); quotations.add(item['quote']); keys.add(item['key'])
        return out
    shared = {p['quote'] for p in good} & {p['quote'] for p in weak}
    if shared:
        conflicts.append('shared_quote')
        good = [p for p in good if p['quote'] not in shared]
        weak = [p for p in weak if p['quote'] not in shared]
    return unique(good), unique(weak), conflicts


def quality(row):
    primary = row['primary_analysis']; scores = primary.get('analysis') or {}
    score = primary.get('score')
    if scores.get('call_cut_short') or score is None or not row['transcript'].strip():
        return 'Данных недостаточно для надёжной оценки качества.'
    applicable = [r for r in scores.get('rows', []) if r.get('applicable', True)]
    passed = sum(bool(r.get('passed')) for r in applicable)
    explanation = (f"Выполнено {passed} из {len(applicable)} применимых критериев." if applicable
                   else 'Подробная проверка критериев не сохранена.')
    return (f"{score}/100 по действующей шкале. {explanation} "
            'Балл описывает качество работы по этой шкале, а результат сделки оценивается отдельно.')


def outcome(row):
    combined = (row.get('auxiliary') or {}).get('combined') or {}
    result = combined.get('outcome') or {}
    if result.get('confirmed') and quote(result.get('evidence'), row['transcript']):
        return clean(result.get('description'), 320), True
    # A saved short report is a summary of the transcript, not a second score.
    brief = row['primary_analysis'].get('short_report') or {}
    text = clean(brief.get('result'), 300)
    return text or 'Подтверждённый результат по доступной записи не установлен.', False


def summary(row):
    good, weak, conflicts = observations(row)
    brief, confirmed = outcome(row)
    readable = ((row.get('auxiliary') or {}).get('combined') or {}).get('readable_report') or {}
    conversation = clean(readable.get('summary'), 240)
    # Reject unsupported quotes or numbers in the otherwise plain short summary.
    quoted = re.findall(r'«([^»]+)»|"([^"\n]+)"', conversation)
    if any(not quote(a or b, row['transcript']) for a, b in quoted) or any(n not in row['transcript'] for n in re.findall(r'\d+(?:[.,:]\d+)*', conversation)):
        conversation = ''
    lines = [header(row)]
    if conversation and conversation != brief and not quoted:
        lines += ['', 'О разговоре: ' + conversation]
    lines += ['', 'Итог: ' + brief, '', 'Качество: ' + quality(row)]
    if good:
        lines += ['', 'Что получилось:']
        for item in good[:2]:
            lines.append('• ' + (clean(item['text'], 150) or 'Подтверждённое сильное действие') + ' — «' + clean(item['quote'], 145, expand=False) + '».')
    else:
        lines += ['', 'Для уверенного вывода о сильных действиях недостаточно подтверждённых реплик.']
    if weak:
        lines += ['', 'Что улучшить:']
        for item in weak[:3]:
            lines.append('• ' + (clean(item['text'], 150) or 'Упущение в разговоре') + ' — «' + clean(item['quote'], 145, expand=False) + '».')
    else:
        lines += ['', 'Подтверждённых ошибок в доступных наблюдениях не найдено.' if row['transcript'].strip()
                  else 'Запись не позволяет проверить ошибки.']
    actions = list(dict.fromkeys(filter(None, [clean(readable.get('next_focus'), 220),
                                             clean(readable.get('exercise'), 220)])))
    example = next((w['suggestion'] for w in weak if w['suggestion']), '')
    if not example:
        example = clean((((row.get('auxiliary') or {}).get('combined') or {}).get('coaching') or {}).get('say_instead'), 260)
    if weak and not actions:
        actions = [TITLES.get(w['key']) for w in weak[:2] if w['key'] in TITLES]
    if actions:
        lines += ['', 'Следующий разговор:'] + ['• ' + a for a in actions[:3]]
    if example:
        lines.append('Пример улучшенной реплики: «' + example + '».')
    limitations = (row.get('auxiliary') or {}).get('limitations') or []
    if conflicts:
        limitations = ['Методики расходятся в оценке части действий. Спорные наблюдения исключены; балл оставлен по действующей шкале.'] + limitations
    # Different sales stages are precisely why the additional methodology exists.
    if (row.get('auxiliary') or {}).get('combined'):
        limitations += ['Дополнительная методика учитывает этап продажи; её наблюдения не изменяют балл основной шкалы.']
        stage = (((row.get('auxiliary') or {}).get('combined') or {}).get('classification') or {}).get('stage')
        if stage in {'presentation', 'demo_preparation', 'demo_results', 'support'}:
            limitations += ['Основная шкала ориентирована на назначение встречи и ограниченно применима к этому этапу.']
    if limitations:
        lines += ['', 'Ограничение: ' + clean(' '.join(limitations), 370)]
    # Bound the text BEFORE escaping. Escaped HTML is never cut mid-tag/entity.
    text = redact_phone('\n'.join(lines), row)
    if units(text) > 3800:
        # Compact whole observations and advice first, preserving the report sections.
        lines = [line if units(line) <= 260 else clean(line, 190, expand=False) for line in lines]
        text = redact_phone('\n'.join(lines), row)
    if units(html.escape(text)) > 4000:
        # Bound escaped text too, without cutting HTML entities or citations.
        lines = [clean(line, 155, expand=False) if units(html.escape(line)) > 230 else line for line in lines]
        text = redact_phone('\n'.join(lines), row)
    if units(html.escape(text)) > 4000:
        raise ValueError('Ordinary report exceeded Telegram limit')
    return html.escape(text)


def expanded(row):
    """Generate from immutable evidence already saved, without another paid request."""
    primary = row['primary_analysis']; analysis = primary.get('analysis') or {}
    aux = row.get('auxiliary') or {}
    lines = [header(row), '', 'Расширенный обзор', '', 'Основная шкала', quality(row),
             'Коммерческий результат и качество разговора — разные показатели.', '', 'Критерии и подтверждения:']
    statuses = {True: 'Выполнено', False: 'Не выполнено'}
    for r in analysis.get('rows') or []:
        status = statuses[bool(r.get('passed'))] if r.get('applicable', True) else 'Неприменимо'
        lines.append('• ' + (clean(r.get('title')) or TITLES.get(r.get('key'), 'Критерий')) + ': ' + status + '.')
        evidence = quote(r.get('evidence'), row['transcript'])
        if evidence:
            lines.append('  «' + evidence + '»')
            explanation = observation_text(r.get('evidence'), evidence)
            if explanation and explanation != evidence:
                lines.append('  Сохранённое пояснение: ' + explanation)
        elif r.get('applicable', True):
            lines.append('  Дословное подтверждение этого вывода не найдено; вывод требует проверки по записи.')
    # Explicitly distinguish the independent scales; no invented common score.
    for key, label in (('course', 'Методика курса: применимость по роли и этапу'),
                       ('combined', 'Дополнительная методика: этапы разговора и рекомендации')):
        data = aux.get(key)
        if not data:
            continue
        from methodology.profiles import ROLE_LABELS, PRODUCT_LABELS, STAGE_LABELS
        classification = data.get('classification') or {}
        lines += ['', label, 'Числовой балл этой методики не откалиброван. Это самостоятельная проверка.',
                  ROLE_LABELS.get(classification.get('sales_role'), 'Роль не определена') + ' · ' +
                  PRODUCT_LABELS.get(classification.get('product'), 'Продукт не определён') + ' · ' +
                  STAGE_LABELS.get(classification.get('stage'), 'Этап не определён')]
        for r in data.get('rows') or []:
            status = {'passed': 'выполнено', 'failed': 'не выполнено', 'not_applicable': 'неприменимо',
                      'insufficient_data': 'недостаточно данных'}.get(r.get('status'), 'недостаточно данных')
            lines += [clean(r.get('title')) + ': ' + status + '.', clean(r.get('reason'), 650)]
            evidence = quote(r.get('evidence'), row['transcript'])
            if evidence:
                lines.append('«' + evidence + '»')
        step = data.get('next_step') or {}
        if (data.get('outcome') or {}).get('confirmed'):
            lines.append('Следующий шаг подтверждён репликой клиента.')
            for field, title in (('action', 'Действие'), ('responsible', 'Ответственный'), ('deadline', 'Срок'), ('channel', 'Канал')):
                if clean(step.get(field)):
                    lines.append(title + ': ' + clean(step[field]))
        else:
            lines.append('Полный следующий шаг этой проверкой не подтверждён.')
        for field, title in (('say_instead', 'Пример улучшенной реплики'), ('repeat_scenario', 'Что потренировать')):
            value = clean((data.get('coaching') or {}).get(field), 650)
            if value:
                lines.append(title + ': ' + value)
    combined = aux.get('combined') or {}
    readable = combined.get('readable_report') or {}
    if readable:
        lines += ['', 'Ключевые моменты разговора:']
        for item in readable.get('moments') or []:
            actual = quote(item.get('quote'), row['transcript'])
            if actual:
                lines += ['«' + actual + '»', clean(item.get('what')), clean(item.get('why'))]
                reaction = quote(item.get('reaction_quote'), row['transcript'])
                if reaction:
                    lines.append('Ответ клиента: «' + reaction + '»')
                if clean(item.get('say_instead')):
                    lines.append('Пример другой реплики: «' + clean(item['say_instead']) + '»')
    detailed = primary.get('detailed_report') or {}
    manager = detailed.get('for_manager') or {}
    if manager:
        lines += ['', 'Сохранённые подробности основного разбора:']
        for item in manager.get('did_well') or []:
            actual = quote(item.get('quote'), row['transcript'])
            if actual:
                lines += [clean(item.get('what')), '«' + actual + '»', clean(item.get('why'))]
        for item in manager.get('moments') or []:
            actual = quote(item.get('quote'), row['transcript'])
            if actual:
                lines += ['«' + actual + '»', clean(item.get('why'))]
                if clean(item.get('say_instead')):
                    lines.append('Пример другой реплики: «' + clean(item['say_instead']) + '»')
        for key, title in (('practice', 'Предложенная тренировка'), ('next_call_focus', 'Фокус следующего разговора')):
            if clean(manager.get(key), 650):
                lines.append(title + ': ' + clean(manager[key], 650))
    head = detailed.get('for_head') or {}
    if clean(head.get('narrative'), 2400):
        lines += ['', 'Сохранённое объяснение ключевых этапов:', clean(head['narrative'], 2400)]
    point = head.get('turning_point') or {}
    actual = quote(point.get('quote'), row['transcript'])
    if actual:
        lines += ['', 'Ключевой момент: «' + actual + '»', clean(point.get('what_happened'), 650)]
        if clean(point.get('alternative'), 650):
            lines.append('Пример другого ответа: ' + clean(point['alternative'], 650))
    for item in head.get('skill_gaps') or []:
        actual = quote(item.get('evidence'), row['transcript'])
        if actual:
            lines += ['', clean(item.get('skill')), '«' + actual + '»', clean(item.get('recurring_risk'))]
    lines += ['', 'Договорённости и возражения:']
    result, confirmed = outcome(row)
    lines.append(result)
    if not confirmed:
        lines.append('Описание результата основной моделью не равно подтверждению полного следующего шага дополнительной методикой.')
    for item in combined.get('objections') or analysis.get('objections') or []:
        actual = quote(item.get('evidence') or item.get('text'), row['transcript'])
        if actual:
            lines.append('Возражение: «' + actual + '»')
            response = quote(item.get('response'), row['transcript'])
            if response:
                lines.append('Ответ: «' + response + '»')
    _, _, conflicts = observations(row)
    if conflicts:
        lines += ['', 'Ограничение: основная и дополнительная проверки расходятся по части действий. Спорные выводы не объединены.']
    lines += ['', 'Ограничения:'] + (aux.get('limitations') or [])
    lines.append('Показаны доступные сохранённые данные именно этого запуска. Отсутствующие объяснения и таймкоды не придуманы.')
    return redact_phone('\n'.join(line for line in lines if isinstance(line, str)), row)


def keyboard(run_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Расширенный обзор', callback_data=f'uv:e:{run_id}')]])


def back_keyboard(run_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='К краткому разбору', callback_data=f'uv:s:{run_id}'),
        InlineKeyboardButton(text='Закрыть обзор', callback_data=f'uv:x:{run_id}')]])
