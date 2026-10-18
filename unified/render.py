"""One compact, safe report and a cached overview of the exact saved run."""
from __future__ import annotations

import html
import hashlib
import re
from datetime import datetime
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from methodology.evaluation import evidence_valid

FORMAT_VERSION = 'combined_style_20261004_v2'
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
    if (row.get('configuration') or {}).get('show_full_phone'):
        return text
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
    phone = clean(meta.get('phone'), 50, expand=False) or 'номер не указан'
    if not (row.get('configuration') or {}).get('show_full_phone'):
        phone = mask_phone(phone)
    zone = clean(meta.get('timezone'), 45, expand=False)
    return (f"📞 {clean(meta.get('manager'), 75) or 'Менеджер не указан'} · {seconds // 60}:{seconds % 60:02d}\n"
            f"{date}" + (f" · {zone}" if zone else '') + f"\nКлиент: {phone}")



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


def verdict(row):
    """The established outcome/key-failure verdict, never a made-up score threshold."""
    primary = row['primary_analysis']
    if primary.get('analysis', {}).get('call_cut_short') or not row['transcript'].strip():
        return '❔ Данных недостаточно для оценки'
    return {'✅': '✅ Следующий шаг достигнут', '⚠️': '⚠️ Есть что улучшить',
            '❌': '❌ Нужен разбор ключевых упущений'}.get(primary.get('level'), '❔ Данных недостаточно для оценки')


def summary(row):
    """Use the combined report's sections; keep the active score and pinned sources."""
    from .legacy_display import selected, display
    if selected(row):
        return display(row)
    from methodology.profiles import PRODUCT_LABELS, STAGE_LABELS, ROLE_LABELS
    good, weak, conflicts = observations(row)
    combined = (row.get('auxiliary') or {}).get('combined') or {}
    readable = combined.get('readable_report') or {}
    brief, confirmed = outcome(row)
    conversation = clean(readable.get('summary'), 240)
    quoted = re.findall(r'«([^»]+)»|"([^"\n]+)"', conversation)
    if any(not quote(a or b, row['transcript']) for a, b in quoted) or any(n not in row['transcript'] for n in re.findall(r'\d+(?:[.,:]\d+)*', conversation)):
        conversation = ''
    classification = combined.get('classification') or {}
    lookup = {quote(item.get('quote'), row['transcript']): item for name in ('strengths', 'moments')
              for item in readable.get(name) or [] if quote(item.get('quote'), row['transcript'])}
    miss = readable.get('main_miss') or {}
    main = next((w for w in weak if w['key'] == miss.get('criterion')), None) if miss else None
    if not main and weak:
        main = weak[0]
    main_quote = main['quote'] if main else None
    moments = [w for w in weak if w['key'] != miss.get('criterion')] if main and miss else weak
    if not moments and main and lookup.get(main_quote, {}).get('say_instead'):
        moments = [main]
    actions = list(dict.fromkeys(filter(None, [clean(readable.get('next_focus'), 220), clean(readable.get('exercise'), 220)])))
    if not actions:
        actions = list(dict.fromkeys(TITLES[w['key']] for w in weak[:2] if w['key'] in TITLES))
    leader = readable.get('leader') or {}
    leader_supported = any(quote(q, row['transcript']) for q in leader.get('quotes') or [])
    causes = {'manager': 'Действия менеджера', 'script': 'Сценарий разговора',
              'base': 'Соответствие контакта предложению', 'insufficient_data': 'Причина пока не установлена'}
    notes = list((row.get('auxiliary') or {}).get('limitations') or []) + list(readable.get('limits') or []) + list(readable.get('validation_warnings') or [])
    if conflicts:
        notes.insert(0, 'Методики расходятся в оценке части действий. Спорные наблюдения исключены; балл оставлен по действующей шкале.')
    if combined and not combined.get('narrow_profile_applied', True):
        notes.append('Продукт или этап определён неуверенно: оценены только общие действия.')
    if classification.get('stage') in {'presentation', 'demo_preparation', 'demo_results', 'support'}:
        notes.append('Основной балл ориентирован на назначение встречи и ограниченно применим к этому этапу; предметные рекомендации учитывают задачу разговора.')
    if (combined.get('honesty') or {}).get('unverified_claims'):
        notes.append('Коммерческие обещания требуют проверки по утверждённым условиям.')

    def compose(short=False):
        blocks = []
        limit = 110 if short else 170
        quote_limit = 85 if short else 150
        def section(title, lines):
            body = '\n'.join(line for line in lines if line)
            plain = redact_phone(body, row)
            blocks.append('<b>' + html.escape(title) + '</b>\n' + html.escape(plain))
        blocks.append(html.escape(header(row)))
        result_lines = [conversation or brief]
        if conversation and confirmed and brief != conversation:
            result_lines.append('Результат: ' + clean(brief, 160 if short else 240))
        if combined and classification:
            result_lines.append(' · '.join(clean(labels.get(classification.get(key)), 65) for key, labels in
                               (('product', PRODUCT_LABELS), ('stage', STAGE_LABELS), ('sales_role', ROLE_LABELS))))
        if combined and not confirmed:
            result_lines.append('Конкретный следующий шаг с согласием клиента не подтверждён.')
        section('🎯 Итог звонка', result_lines)
        score = row['primary_analysis'].get('score')
        quality_lines = [verdict(row)]
        if score is not None and not row['primary_analysis'].get('analysis', {}).get('call_cut_short') and row['transcript'].strip():
            quality_lines += [f'Качество по действующей шкале: {score}/100.', 'Знак учитывает результат и ключевые упущения; балл — выполнение критериев.']
        section('📊 Оценка звонка', quality_lines)
        lines = []
        for item in good[:2]:
            detail = lookup.get(item['quote'], {})
            lines += ['• ' + (clean(item['text'], limit) or 'Подтверждённое сильное действие'),
                      '«' + clean(item['quote'], quote_limit, expand=False) + '»', clean(detail.get('why'), limit)]
        section('✅ Что получилось', lines or ['В записи недостаточно подтверждений для отдельного сильного действия.'])
        if main:
            lines = [clean(miss.get('explanation') if miss else main['text'], 150 if short else 260)]
            if not any(w['quote'] == main_quote for w in moments[:2]):
                lines.append('«' + clean(main_quote, quote_limit, expand=False) + '»')
            section('📌 Главный упущенный момент', lines)
        for index, item in enumerate(moments[:2], 1):
            detail = lookup.get(item['quote'], {})
            lines = [clean(item['text'], limit), 'Менеджер: «' + clean(item['quote'], quote_limit, expand=False) + '»']
            reaction = quote(detail.get('reaction_quote'), row['transcript'])
            if reaction:
                lines.append('Клиент: «' + clean(reaction, quote_limit, expand=False) + '»')
            lines.append(clean(detail.get('why'), limit))
            if item['suggestion']:
                lines.append('Можно сказать: «' + clean(item['suggestion'], 145 if short else 220) + '»')
            section('💬 Что изменить' + (f' · {index}' if len(moments[:2]) > 1 else ''), lines)
        if not weak:
            section('💬 Что изменить', ['Подтверждённых ошибок в доступных наблюдениях не найдено.' if row['transcript'].strip()
                                        else 'Запись не позволяет проверить ошибки.'])
        if actions:
            section('🎯 Что сделать дальше', [('На следующий разговор: ' if i == 0 else 'Короткая практика: ') + clean(a, limit)
                                              for i, a in enumerate(actions[:2])])
        leader_lines = [causes.get(leader.get('cause'), 'Причина пока не установлена') + ': ' +
                        (clean(leader.get('reason'), limit) if leader_supported and leader.get('cause') != 'insufficient_data'
                         else 'По этой записи нельзя уверенно отделить недостаток навыка от ограничений сценария или контакта.')]
        if clean(leader.get('action')):
            leader_lines.append(clean(leader.get('action'), limit))
        section('👤 Руководителю', leader_lines)
        if notes:
            section('ℹ️ Границы оценки', ['• ' + clean(' '.join(dict.fromkeys(notes)), 260 if short else 420)])
        return '\n\n'.join(blocks)
    text = compose()
    if units(text) > 4000:
        text = compose(short=True)
    if units(text) > 4000:
        raise ValueError('Ordinary report exceeded Telegram limit')
    return text


def expanded(row):
    """One source-backed narrative; saved analyses remain diagnostic data."""
    from .legacy_display import selected, display
    if selected(row):
        return display(row, detailed=True)
    from .extended_review import render as render_extended
    return render_extended(row)


def keyboard(run_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='🔎 Расширенный обзор', callback_data=f'uv:e:{run_id}')]])


def back_keyboard(run_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='К краткому разбору', callback_data=f'uv:s:{run_id}'),
        InlineKeyboardButton(text='Закрыть обзор', callback_data=f'uv:x:{run_id}')]])


def reopen_keyboard(run_id, generation):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Показать обзор здесь', callback_data=f'uv:r:{run_id}:{generation}')], [
        InlineKeyboardButton(text='К краткому разбору', callback_data=f'uv:s:{run_id}')]])
