"""Кабинет клиента колл-центра: его лист в таблице «Прозвон лидов».

Сводка таблицы даёт список клиентов и тарифы, лист клиента — номера и три
попытки звонка по каждому. Клиент видит только свой лист: имя листа берём из
сессии, а не из запроса.
"""
from __future__ import annotations

import re
from collections import defaultdict

from . import gsheets

INTERESTED, CALLBACK, NOT_RELEVANT = 'Заинтересованный', 'Перезвон', 'Неактуально'


def ru_date(value) -> str:
    m = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', str(value or ''))
    return f'{m[3]}-{m[2]}-{m[1]}' if m else ''


def money(value) -> float:
    text = re.sub(r'[^\d,.-]', '', str(value or '')).replace(',', '.')
    try:
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


def cell(row, i) -> str:
    return str(row[i]).strip() if 0 <= i < len(row) and row[i] is not None else ''


def parse_summary(rows) -> dict:
    """Тарифы и итог по клиентам из листа «Сводка»."""
    tariffs, clients = [], []
    labels = [cell(r, 0) for r in rows]
    if 'Вид звонка' in labels:
        for r in rows[labels.index('Вид звонка') + 1:]:
            if not cell(r, 0):
                break
            tariffs.append({'name': cell(r, 0), 'price': money(cell(r, 1))})
    if 'Клиент (название листа)' in labels:
        for r in rows[labels.index('Клиент (название листа)') + 1:]:
            name = cell(r, 0)
            if not name or name == 'ИТОГО':
                break
            clients.append({'name': name, 'first': int(money(cell(r, 1))), 'repeat': int(money(cell(r, 2))),
                            'interested': int(money(cell(r, 3))), 'sum': money(cell(r, 4))})
    return {'tariffs': tariffs, 'clients': clients}


def outcome(attempts: list[str]) -> str:
    done = [a for a in attempts if a]
    if not done:
        return 'not_called'
    if INTERESTED in done:
        return 'interested'
    last = done[-1]
    if last == CALLBACK:
        return 'callback'
    if last == NOT_RELEVANT:
        return 'not_relevant'
    if all(a.startswith('НДЗ') for a in done):
        return 'no_answer'
    return 'other'


def parse_client(rows) -> dict:
    head = rows[0] if rows else []

    def ix(name):
        return head.index(name) if name in head else -1
    I = {k: ix(v) for k, v in {
        'id': 'id', 'project': 'Проект', 'tag': 'Тег проекта', 'phone': 'Телефон', 'remind': 'Напоминание',
        'note': 'Заметка', 'comment': 'Комментарий', 'c1': 'Звонок 1 (первичный)', 'c2': 'Звонок 2 (повторный)',
        'c3': 'Звонок 3 (повторный)', 'sum': 'Сумма', 'base': 'Дата базы', 'd1': 'Дата звонка 1',
        'd2': 'Дата звонка 2', 'd3': 'Дата звонка 3'}.items()}
    counts = defaultdict(int)
    bases = defaultdict(lambda: defaultdict(int))
    days = defaultdict(lambda: defaultdict(int))
    interested, callbacks = [], []
    cost, since = 0.0, ''
    for r in rows[1:]:
        if not cell(r, I['id']).isdigit():
            continue
        attempts = [cell(r, I['c1']), cell(r, I['c2']), cell(r, I['c3'])]
        dates = [ru_date(cell(r, I['d1'])), ru_date(cell(r, I['d2'])), ru_date(cell(r, I['d3']))]
        base = ru_date(cell(r, I['base']))
        since = min(since or base, base or since)
        kind = outcome(attempts)
        reached = any(a and not a.startswith('НДЗ') for a in attempts)
        counts['total'] += 1
        counts[kind] += 1
        b = bases[base or '']
        b['total'] += 1
        if kind != 'not_called':
            counts['called'] += 1
            b['called'] += 1
        if reached:
            counts['reached'] += 1
            b['reached'] += 1
        if kind == 'interested':
            b['interested'] += 1
        for i, (a, d) in enumerate(zip(attempts, dates)):
            if a and d:
                day = days[d]
                day['calls'] += 1
                day['first' if i == 0 else 'repeat'] += 1
                if not a.startswith('НДЗ'):
                    day['reached'] += 1
                if a == INTERESTED:
                    day['interested'] += 1
        cost += money(cell(r, I['sum']))
        lead = {'phone': cell(r, I['phone']), 'source': cell(r, I['tag']) or cell(r, I['project']),
                'comment': cell(r, I['comment']) or cell(r, I['note']), 'base': base,
                'attempts': attempts, 'last': max([d for d in dates if d] or [''])}
        if kind == 'interested':
            interested.append(lead)
        elif kind == 'callback':
            callbacks.append({**lead, 'remind': cell(r, I['remind'])})
    interested.sort(key=lambda x: x['last'], reverse=True)
    callbacks.sort(key=lambda x: x['last'], reverse=True)
    return {
        'counts': {k: counts[k] for k in ('total', 'called', 'not_called', 'reached', 'interested', 'callback',
                                          'not_relevant', 'no_answer', 'other')},
        'cost': round(cost, 2), 'since': since,
        'bases': [{'date': d, **v} for d, v in sorted(bases.items(), reverse=True)],
        'days': [{'day': d, **v} for d, v in sorted(days.items(), reverse=True)],
        'interested': interested, 'callbacks': callbacks,
    }


async def summary() -> dict:
    sheet = gsheets.leads_sheet()
    if not sheet:
        raise LookupError('Таблица колл-центра не подключена.')
    return parse_summary(await gsheets.values(sheet, 'Сводка!A1:F80'))


async def cabinet(name: str) -> dict:
    info = await summary()
    row = next((c for c in info['clients'] if c['name'] == name), None)
    if row is None:
        raise LookupError('Такого клиента нет в таблице колл-центра.')
    rows = await gsheets.values(gsheets.leads_sheet(), f"'{name.replace(chr(39), chr(39) * 2)}'!A1:P6000")
    return {'name': name, 'summary': row, 'tariffs': info['tariffs'], **parse_client(rows)}
