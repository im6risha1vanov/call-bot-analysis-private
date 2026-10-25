"""Кабинет клиента колл-центра: его лист в таблице «Прозвон лидов» или его номера в базе.

Сводка таблицы даёт список клиентов и тарифы, лист клиента — номера и три
попытки звонка по каждому. Клиент видит только свой лист: имя листа берём из
сессии, а не из запроса. Компания без таблицы колл-центра ведёт клиентов в базе (crm/callcenter_db.py):
она отдаёт те же строки, что лист, и всё ниже считается одним кодом.
"""
from __future__ import annotations

import asyncio
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
    waiting = reached_first = 0
    sources = defaultdict(lambda: defaultdict(int))
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
        if kind == 'no_answer' and len([a for a in attempts if a]) < 3:
            waiting += 1  # недозвон, а третьей попытки ещё не было
        if attempts[0] and not attempts[0].startswith('НДЗ'):
            reached_first += 1
        src = sources[cell(r, I['tag']) or cell(r, I['project']) or '']
        src['total'] += 1
        src['reached'] += reached
        src['interested'] += kind == 'interested'
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
        'cost': round(cost, 2), 'since': since, 'waiting': waiting, 'reached_first': reached_first,
        'sources': sorted(({'name': k, **v} for k, v in sources.items()), key=lambda x: -x['total']),
        'bases': [{'date': d, **v} for d, v in sorted(bases.items(), reverse=True)],
        'days': [{'day': d, **v} for d, v in sorted(days.items(), reverse=True)],
        'interested': interested, 'callbacks': callbacks,
    }


async def sheet_of(pool, client_id: int | None) -> str:
    """Таблица колл-центра компании (у нашей — KITCHEN_SHEET_ID из .env)."""
    from . import company
    sheet = await company.setting(pool, client_id, 'callcenter_sheet')
    if not sheet:
        raise LookupError('Таблица колл-центра не подключена.')
    gsheets.allow(sheet)
    return sheet


class SheetSource:
    """Таблица «Прозвон лидов»: лист «Сводка» и по листу на клиента."""
    mode = 'sheet'

    def __init__(self, sheet: str):
        self.sheet = sheet

    async def summary(self) -> dict:
        return parse_summary(await gsheets.values(self.sheet, 'Сводка!A1:F80'))

    async def rows(self, name: str) -> list:
        return await gsheets.values(self.sheet, f"'{name.replace(chr(39), chr(39) * 2)}'!A1:P6000")


async def source_of(pool, client_id: int | None):
    """Откуда колл-центр компании: её таблица, а без таблицы — база (если модуль подключён)."""
    from . import callcenter_db, company
    if await company.setting(pool, client_id, 'callcenter_sheet'):
        return SheetSource(await sheet_of(pool, client_id))
    if await company.has_module(pool, client_id, 'callcenter'):
        return callcenter_db.DbSource(pool, client_id)
    raise LookupError('Колл-центр не подключён.')


def _source(src):
    return SheetSource(src) if isinstance(src, str) else src


async def summary(src) -> dict:
    return await _source(src).summary()


async def client_names(pool, client_id: int | None) -> list[str]:
    """Клиенты колл-центра компании; нет колл-центра — пусто."""
    try:
        return [c['name'] for c in (await summary(await source_of(pool, client_id)))['clients']]
    except LookupError:
        return []


async def cabinet(src, name: str) -> dict:
    src = _source(src)
    info = await src.summary()
    row = next((c for c in info['clients'] if c['name'] == name), None)
    if row is None:
        raise LookupError('Такого клиента нет в колл-центре.')
    return {'name': name, 'summary': row, 'tariffs': info['tariffs'], **parse_client(await src.rows(name))}


async def callcenter(src) -> dict:
    """Колл-центр целиком для директора: все клиенты колл-центра в одном ответе."""
    src = _source(src)
    info = await src.summary()
    sheets = await asyncio.gather(*(src.rows(c['name']) for c in info['clients']), return_exceptions=True)
    clients, callbacks, sources = [], [], defaultdict(lambda: defaultdict(int))
    days = defaultdict(lambda: defaultdict(int))
    bases = []
    total = defaultdict(int)
    for row, rows in zip(info['clients'], sheets):
        if isinstance(rows, LookupError):
            rows = []
        elif isinstance(rows, BaseException):
            raise rows
        p = parse_client(rows)
        k = p['counts']
        clients.append({'name': row['name'], 'since': p['since'], 'leads': k['total'], 'called': k['called'],
                        'reached': k['reached'], 'interested': k['interested'], 'callback': k['callback'],
                        'waiting': p['waiting'], 'not_called': k['not_called'], 'sum': row['sum']})
        for key in ('called', 'reached', 'interested', 'total'):
            total[key] += k[key]
        total['reached_first'] += p['reached_first']
        total['sum'] += row['sum']
        for d in p['days']:
            for key in ('first', 'repeat', 'reached', 'interested'):
                days[d['day']][key] += d.get(key, 0)
        callbacks += [{**c, 'client': row['name']} for c in p['callbacks']]
        for s in p['sources']:
            for key in ('total', 'reached', 'interested'):
                sources[(row['name'], s['name'])][key] += s[key]
        bases += [{**b, 'client': row['name']} for b in p['bases'] if b['date']]
    callbacks.sort(key=lambda x: x['last'], reverse=True)
    return {
        'mode': getattr(src, 'mode', 'db'), 'tariffs': info['tariffs'], 'clients': clients, 'total': dict(total),
        'days': [{'day': d, **v} for d, v in sorted(days.items())][-45:],
        'callbacks': callbacks[:150],
        'sources': sorted(({'client': c, 'name': n, **v} for (c, n), v in sources.items()), key=lambda x: -x['total'])[:60],
        'bases': sorted(bases, key=lambda x: x['date'], reverse=True)[:40],
    }
