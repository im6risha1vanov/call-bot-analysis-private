"""Тексты разбора: менеджеру (наставник, до пяти строк), РОПу (сухо), «Подробнее».

Основной текст пишет модель. Код только добавляет шапку и экранирует HTML; если
выводы модели не прошли проверку (engine.validate), сообщение собирается из
проверенных полей.
"""
from __future__ import annotations

import collections
from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from reports import esc, fmt_phone

OUTCOME_LABELS = {'deal': 'сделка', 'meeting': 'встреча', 'callback_date': 'перезвон в конкретный день',
                  'proposal': 'КП', 'dmp_contact': 'контакт ЛПР'}
CATEGORY_LABELS = {
    'no_callback_date': 'не закреплён день перезвона',
    'lost_dmp': 'не вышли на ЛПР',
    'brush_off_accepted': 'приняли отговорку без вопроса',
    'offer_not_fit': 'предложение не под бизнес клиента',
    'price_not_answered': 'не ответили на вопрос о цене',
    'ignored_interest': 'не развили интерес клиента',
    'no_reason': 'клиент не понял, зачем звонят',
    'unanswered_question': 'вопрос клиента без ответа',
    'pitch_instead_of_step': 'рассказ о продукте вместо следующего шага',
    'no_close': 'не предложили шаг в конце',
    'other': 'другое',
}
SPEAKERS = {'manager': 'Ты', 'client': 'Клиент'}
MAX_LINES = 6


def mark(data: dict) -> str:
    if data.get('success'):
        return '✅'
    return {'hot': '🔥', 'normal': '❌'}.get(data.get('chance'), '⚪')


def header(meta: dict, data: dict, *, with_manager=False) -> str:
    started = meta.get('call_started_at')
    when = started.strftime('%d.%m %H:%M') if isinstance(started, datetime) else ''
    seconds = int(meta.get('duration_seconds') or 0)
    parts = [mark(data)]
    if with_manager:
        parts.append(esc(meta.get('manager') or 'Менеджер не указан'))
    parts += [p for p in (when, f'{seconds // 60}:{seconds % 60:02d}' if seconds else '',
                          esc(fmt_phone(meta.get('client_number')) or '')) if p]
    return ' · '.join(parts)


def moment_line(item: dict) -> str | None:
    if not item or not item.get('quote'):
        return None
    who = SPEAKERS.get(item.get('speaker'), 'Реплика')
    stamp = f"[{item['timecode']}] " if item.get('timecode') else ''
    return f'{stamp}{who}: «{esc(item["quote"])}»'


def from_fields(data: dict) -> list[str]:
    """Запасной текст менеджеру только из проверенных полей."""
    if data.get('success'):
        return ['Есть результат: ' + ', '.join(OUTCOME_LABELS[o] for o in data['outcome']) + '.']
    moment = data.get('key_moment') or {}
    lines = [esc(moment.get('what_happened') or 'Результата нет.'), moment_line(moment)]
    if moment.get('say_instead'):
        lines.append(f'Как можно было: «{esc(moment["say_instead"])}»')
    return [line for line in lines if line]


def manager_text(data: dict) -> str:
    text = (data.get('manager_text') or '').strip()
    if data.get('text_unverified') or not text:
        return '\n'.join(from_fields(data))
    return '\n'.join(esc(line) for line in text.splitlines()[:MAX_LINES] if line.strip())


def manager_message(meta: dict, data: dict) -> str:
    return header(meta, data) + '\n' + manager_text(data)


def rop_message(meta: dict, data: dict) -> str:
    return header(meta, data, with_manager=True) + '\n' + esc(data.get('rop_line') or '')


def details_message(meta: dict, data: dict) -> str:
    body = (data.get('details') or '').strip()
    if data.get('text_unverified') or not body:
        body_lines = from_fields(data)
        for miss in data.get('other_misses') or []:
            body_lines += ['', esc(miss.get('what_happened') or ''), moment_line(miss) or '']
        step = ((data.get('client') or {}).get('next_step') or {})
        if step.get('raw'):
            body_lines += ['', 'Договорённость: ' + esc(step['raw'])]
    else:
        body_lines = [esc(line) for line in body.splitlines()]
    return header(meta, data, with_manager=True) + '\n\n' + '\n'.join(line for line in body_lines if line is not None).strip()


def keyboard(call_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Подробнее', callback_data=f'rv:d:{call_id}')]])


def list_keyboard(items: list[tuple[int, str]]) -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(text=label[:60], callback_data=f'rv:d:{call_id}')] for call_id, label in items[:10]]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def day_counts(rows: list[dict]) -> dict:
    talks = [r for r in rows if r['data'].get('reviewed')]
    outcomes = collections.Counter(o for r in talks for o in r['data'].get('outcome') or [])
    lost = [r for r in talks if not r['data'].get('success') and r['data'].get('chance') in {'hot', 'normal'}]
    return {'talks': len(talks), 'success': sum(bool(r['data'].get('success')) for r in talks),
            'outcomes': outcomes, 'lost': lost, 'hot': sum(r['data'].get('chance') == 'hot' for r in lost)}


def outcomes_text(outcomes: collections.Counter) -> str:
    return ', '.join(f'{OUTCOME_LABELS[k]} {v}' for k, v in outcomes.most_common())


def manager_summary(name: str, rows: list[dict], tomorrow: str | None = None) -> str | None:
    """Вечерняя сводка менеджеру: итог дня, что получилось, одна вещь на завтра."""
    counts = day_counts(rows)
    if not counts['talks']:
        return None
    out = [f'<b>Итог дня</b> · {esc(name)}',
           f"Разговоров: {counts['talks']}, с результатом: {counts['success']}"
           + (f" ({outcomes_text(counts['outcomes'])})" if counts['outcomes'] else '')]
    positives = [p for r in sorted(rows, key=lambda r: not r['data'].get('success'))
                 for p in r['data'].get('positives') or []][:2]
    if positives:
        out += ['', '<b>Что получилось</b>']
        for p in positives:
            out.append('• ' + esc(p.get('why') or '') + (f' «{esc(p["quote"])}»' if p.get('quote') else ''))
    categories = collections.Counter((r['data'].get('key_moment') or {}).get('category') for r in counts['lost'])
    categories.pop(None, None)
    if categories:
        category, times = categories.most_common(1)[0]
        example = next((r['data']['key_moment'] for r in counts['lost']
                        if (r['data'].get('key_moment') or {}).get('category') == category
                        and r['data']['key_moment'].get('say_instead')), None)
        out += ['', '<b>На завтра</b>',
                f"Чаще всего сегодня: {CATEGORY_LABELS.get(category, 'другое')} ({times})."]
        if example:
            out.append(f'Например, так: «{esc(example["say_instead"])}»')
    callbacks = [r for r in rows if tomorrow and (((r['data'].get('client') or {}).get('next_step') or {}).get('date') == tomorrow)]
    if callbacks:
        out += ['', '<b>Договорённости на завтра</b>']
        for r in callbacks:
            step = r['data']['client']['next_step']
            who = (r['data'].get('client') or {}).get('company') or fmt_phone(r['meta'].get('client_number')) or 'клиент'
            out.append(f"• {esc(who)}: {esc(step.get('time') or '')} {esc(step.get('raw') or '')}".rstrip())
    return '\n'.join(out)


def rop_summary(rows: list[dict]) -> tuple[str, list[tuple[int, str]]] | None:
    """Вечерняя сводка РОПу: цифры по менеджерам и список потерь с шансом."""
    by_manager = collections.defaultdict(list)
    for r in rows:
        by_manager[r['meta'].get('manager') or 'Менеджер не указан'].append(r)
    total = day_counts(rows)
    if not total['talks']:
        return None
    out = ['<b>Итог дня по отделу</b>',
           f"Разговоров: {total['talks']}, с результатом: {total['success']}, потеряно с шансом: {len(total['lost'])}"]
    for name, items in sorted(by_manager.items(), key=lambda kv: -len(kv[1])):
        c = day_counts(items)
        if c['talks']:
            out.append(f"• {esc(name)}: {c['talks']} / результат {c['success']} / потери {len(c['lost'])}"
                       + (f" (горячих {c['hot']})" if c['hot'] else ''))
    categories = collections.Counter((r['data'].get('key_moment') or {}).get('category') for r in total['lost'])
    categories.pop(None, None)
    if categories:
        out += ['', 'Где теряем: ' + ', '.join(f'{CATEGORY_LABELS.get(k, k)} {v}' for k, v in categories.most_common(3))]
    lost = sorted(total['lost'], key=lambda r: r['data'].get('chance') != 'hot')
    if lost:
        out += ['', '<b>Потери с шансом</b>']
        for r in lost[:15]:
            out.append(rop_message(r['meta'], r['data']))
    buttons = [(r['call_id'], f"{mark(r['data'])} {r['meta'].get('manager') or ''} {header_time(r['meta'])}".strip()) for r in lost]
    return '\n'.join(out), buttons


def header_time(meta: dict) -> str:
    started = meta.get('call_started_at')
    return started.strftime('%H:%M') if isinstance(started, datetime) else ''
