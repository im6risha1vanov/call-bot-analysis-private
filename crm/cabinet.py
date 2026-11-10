"""Кабинет клиента колл-центра: его лист в таблице «Прозвон лидов» или его номера в базе.

Сводка таблицы даёт список клиентов и тарифы, лист клиента — номера и три
попытки звонка по каждому. Клиент видит только свой лист: имя листа берём из
сессии, а не из запроса. Компания без таблицы колл-центра ведёт клиентов в базе (crm/callcenter_db.py):
она отдаёт те же строки, что лист, и всё ниже считается одним кодом.

Фильтры «Колл-центра» — период и сотрудник. Период считается по датам попыток
(дат звонков). Сотрудник — по подтверждённому автору попытки: в базе это
добавочный Mango из ``cc_leads.attempts[].by``, в таблице — колонка «Оператор»,
если она есть, иначе сверка с журналом Mango по телефону, дню и кампании
(crm/callcenter_match.py). Чего источник не хранит и что не подтвердилось,
фильтр не показывает: недоступный фильтр помечается причиной, а не догадками.
"""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from . import callcenter_match, gsheets

INTERESTED, CALLBACK, NOT_RELEVANT = 'Заинтересованный', 'Перезвон', 'Неактуально'
PERIODS = (('today', 'Сегодня'), ('7', '7 дней'), ('30', '30 дней'), ('month', 'Месяц'))
PERIOD_NAMES = {'today': 'сегодня', '7': 'за 7 дней', '30': 'за 30 дней', 'month': 'за месяц'}
PERIOD_KEYS = {key for key, _ in PERIODS}
# Колонки листа «Прозвон лидов»: порядок и названия те же, что в HEAD у crm/callcenter_db.py.
COLUMNS = {'id': 'id', 'project': 'Проект', 'tag': 'Тег проекта', 'phone': 'Телефон', 'remind': 'Напоминание',
           'note': 'Заметка', 'comment': 'Комментарий', 'c1': 'Звонок 1 (первичный)', 'c2': 'Звонок 2 (повторный)',
           'c3': 'Звонок 3 (повторный)', 'sum': 'Сумма', 'base': 'Дата базы', 'd1': 'Дата звонка 1',
           'd2': 'Дата звонка 2', 'd3': 'Дата звонка 3'}
# Оператор звонка в таблице: колонки с такими названиями читаем, если владелец их добавил.
OPERATOR_COLUMNS = ('Оператор', 'Сотрудник', 'Менеджер', 'Оператор звонка', 'Кто звонил')
NO_OPERATOR = ('В Google-таблице «Прозвон лидов» нет колонки оператора, а связать звонки Mango с попытками '
               'не удалось: кто звонил, не подтверждено. Разрез по сотрудникам недоступен — добавьте колонку '
               '«Оператор» в лист или задайте подтверждённые добавочные, чтобы включить сверку с журналом Mango.')
NO_SUM = ('Сумма в таблице посчитана по лиду целиком, а не по каждому звонку: разделить её по периоду '
          'нельзя. Суммы за период считаются только там, где звонки и цены хранятся по попыткам — '
          'в колл-центре в базе.')
NO_ROBOT = 'Данные робота не запрошены: срез из вкладки «Робот» не считался.'


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


def outcome(results: list[str]) -> str:
    done = [a for a in results if a]
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


def period_range(period: str, today: date | None = None) -> tuple[date, date]:
    """Календарные границы периода включительно, по дню компании."""
    if period not in PERIOD_KEYS:
        raise ValueError('Неизвестный период.')
    today = today or date.today()
    if period == 'today':
        return today, today
    if period == 'month':
        return today.replace(day=1), today
    return today - timedelta(days=int(period) - 1), today


def sheet_operator_column(rows) -> int:
    """Номер колонки «Оператор», если владелец её добавил в лист. Иначе -1."""
    head = rows[0] if rows else []
    for name in OPERATOR_COLUMNS:
        if name in head:
            return head.index(name)
    return -1


def sheet_records(rows) -> list[dict]:
    """Строки листа в записи лидов: попытка = результат, дата и (если есть) оператор."""
    head = rows[0] if rows else []

    def ix(name):
        return head.index(name) if name in head else -1
    I = {key: ix(name) for key, name in COLUMNS.items()}
    op = sheet_operator_column(rows)
    records = []
    for r in rows[1:]:
        if not cell(r, I['id']).isdigit():
            continue
        records.append({
            'id': cell(r, I['id']), 'project': cell(r, I['project']), 'tag': cell(r, I['tag']),
            'phone': cell(r, I['phone']), 'remind': cell(r, I['remind']), 'note': cell(r, I['note']),
            'comment': cell(r, I['comment']), 'base': ru_date(cell(r, I['base'])), 'sum': money(cell(r, I['sum'])),
            'attempts': [{'result': cell(r, I[f'c{i}']), 'date': ru_date(cell(r, I[f'd{i}'])),
                          'by': cell(r, op) if op >= 0 else ''} for i in (1, 2, 3)],
        })
    return records


def attempt_price(result: str, index: int, prices: dict) -> float:
    """Цена одной попытки: заинтересованный по своему тарифу, первая и повторные — по своим."""
    if result == INTERESTED:
        return prices['interested']
    return prices['first'] if index == 0 else prices['repeat']


def parse_records(records, keep=None, prices=None) -> dict:
    """Итоги по номерам.

    keep(attempt) — какие попытки входят в срез (период и сотрудник); None — все.
    prices — цены звонков: тогда сумма среза считается по попыткам. Без цен
    (в таблице) сумма среза не считается: «Сумма» листа делится только по лиду.
    """
    filtered = keep is not None
    counts = defaultdict(int)
    waiting = reached_first = 0
    sources = defaultdict(lambda: defaultdict(int))
    bases = defaultdict(lambda: defaultdict(int))
    days = defaultdict(lambda: defaultdict(int))
    interested, callbacks = [], []
    cost, since, counted = 0.0, '', 0
    per_attempt = filtered and prices is not None
    for rec in records:
        pairs = list(enumerate(rec['attempts']))
        if filtered:
            pairs = [(i, a) for i, a in pairs if keep(a)]
            if not pairs:
                continue
        results = [a['result'] for _, a in pairs]
        done = [a for a in results if a]
        base = rec['base']
        since = min(since or base, base or since)
        kind = outcome(results)
        reached = any(a and not a.startswith('НДЗ') for a in results)
        counts['total'] += 1
        counts[kind] += 1
        counted += 1
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
        if kind == 'no_answer' and len(done) < 3:
            waiting += 1  # недозвон, а третьей попытки ещё не было
        first = rec['attempts'][0] if rec['attempts'] else None
        if filtered:
            # «Первая попытка дозвонилась» — про настоящую первую попытку лида, а не про первую в срезе.
            if first and first['result'] and keep(first) and not first['result'].startswith('НДЗ'):
                reached_first += 1
        elif results[0] and not results[0].startswith('НДЗ'):
            reached_first += 1
        source = rec['tag'] or rec['project'] or ''
        src = sources[source]
        src['total'] += 1
        src['reached'] += reached
        src['interested'] += kind == 'interested'
        for i, a in pairs:
            if a['result'] and a['date']:
                day = days[a['date']]
                day['calls'] += 1
                day['first' if i == 0 else 'repeat'] += 1
                if not a['result'].startswith('НДЗ'):
                    day['reached'] += 1
                if a['result'] == INTERESTED:
                    day['interested'] += 1
        if per_attempt:
            cost += sum(attempt_price(a['result'], i, prices) for i, a in pairs)
        elif not filtered:
            cost += money(rec['sum'])
        lead = {'phone': rec['phone'], 'source': rec['tag'] or rec['project'],
                'comment': rec['comment'] or rec['note'], 'base': base, 'attempts': results,
                'last': max([a['date'] for _, a in pairs if a['date']] or [''])}
        if kind == 'interested':
            interested.append(lead)
        elif kind == 'callback':
            callbacks.append({**lead, 'remind': rec['remind']})
    interested.sort(key=lambda x: x['last'], reverse=True)
    callbacks.sort(key=lambda x: x['last'], reverse=True)
    return {
        'counts': {k: counts[k] for k in ('total', 'called', 'not_called', 'reached', 'interested', 'callback',
                                          'not_relevant', 'no_answer', 'other')},
        'cost': None if filtered and not per_attempt else round(cost, 2),
        'since': since, 'waiting': waiting, 'reached_first': reached_first, 'leads': counted,
        'sources': sorted(({'name': k, **v} for k, v in sources.items()), key=lambda x: -x['total']),
        'bases': [{'date': d, **v} for d, v in sorted(bases.items(), reverse=True)],
        'days': [{'day': d, **v} for d, v in sorted(days.items(), reverse=True)],
        'interested': interested, 'callbacks': callbacks,
    }


def parse_client(rows) -> dict:
    return parse_records(sheet_records(rows))


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
        self.operator_field = False  # колонку «Оператор» ищем в шапке листа, а не выдумываем

    async def summary(self) -> dict:
        return parse_summary(await gsheets.values(self.sheet, 'Сводка!A1:F80'))

    async def rows(self, name: str) -> list:
        # A:Z — если владелец добавит колонку «Оператор» справа, она попадёт в чтение.
        return await gsheets.values(self.sheet, f"'{name.replace(chr(39), chr(39) * 2)}'!A1:Z6000")

    async def records(self, name: str) -> list[dict]:
        rows = await self.rows(name)
        if sheet_operator_column(rows) >= 0:
            self.operator_field = True
        return sheet_records(rows)

    async def sum_prices(self) -> None:
        """Сумма листа считается по лиду, а не по звонку: цен для среза периода нет."""
        return None


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


# ------------------------------------------------------------------ фильтры колл-центра


def norm(value) -> str:
    return re.sub(r'\s+', ' ', str(value or '').lower().replace('ё', 'е')).strip()


def operator_aliases(operator: dict) -> set[str]:
    """Как оператор может быть записан в попытке: добавочный и имя."""
    tokens = {norm(operator.get(key)) for key in ('name', 'short', 'display', 'id')}
    tokens.discard('')
    return tokens


def attempt_belongs(attempt: dict, operator: dict) -> bool:
    by = norm(attempt.get('by'))
    if not by:
        return False
    extension = norm(operator.get('extension'))
    return bool(extension and by == extension) or by in operator_aliases(operator)


def local_zone(name: str = 'Europe/Moscow') -> ZoneInfo:
    """Пояс компании. На Windows без базы часовых поясов — фиксированный UTC+3."""
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — нет tzdata в среде
        from datetime import timedelta as _delta, timezone as _tz
        return _tz(_delta(hours=3), 'MSK')


def operator_state(operator: dict, mode: str, has_column: bool, matched: bool,
                   robot: dict | None = None) -> tuple[bool, str]:
    """Можно ли считать срез по этому сотруднику и что мешает, если нельзя."""
    if operator.get('kind') == 'robot':
        robot = robot or {}
        if robot.get('available'):
            return True, ''
        if has_column and operator.get('extension'):
            return True, ''
        return False, robot.get('reason') or NO_ROBOT
    if mode == 'db':
        if operator.get('extension'):
            return True, ''
        return False, operator.get('note') or 'Добавочный сотрудника не подтверждён.'
    if has_column:
        return True, ''
    if matched:
        if operator.get('extension'):
            return True, ''
        return False, operator.get('note') or 'Добавочный сотрудника не подтверждён.'
    return False, NO_OPERATOR


def keep_attempts(start: date | None, end: date | None, operator: dict | None):
    """Срез попыток: дата звонка внутри периода, автор звонка — выбранный сотрудник."""
    def keep(attempt: dict) -> bool:
        if start is not None:
            day = attempt.get('date') or ''
            if not day or not (start.isoformat() <= day <= end.isoformat()):
                return False
        return attempt_belongs(attempt, operator) if operator else True
    return keep


async def callcenter(src, period: str | None = None, operator: str | None = None,
                     operators: list[dict] | None = None, today: date | None = None,
                     calls: dict | None = None, tz=None, robot: dict | None = None) -> dict:
    """Колл-центр целиком для директора: все клиенты колл-центра в одном ответе.

    Без периода и сотрудника ответ тот же, что раньше. С фильтрами числа считаются
    по попыткам: период — по дате звонка, сотрудник — по подтверждённому автору
    попытки. ``calls`` — звонки Mango для сверки с таблицей (crm/callcenter_match.py),
    ``robot`` — срез вкладки «Робот» (crm/callcenter_robot.py). Ни один из них не
    подменяет числа листа: они показываются отдельно и только с подтверждёнными связями.
    """
    src = _source(src)
    tz = tz or local_zone()
    start = end = None
    if period:
        start, end = period_range(period, today)
    info = await src.summary()
    sheets = await asyncio.gather(*(src.records(c['name']) for c in info['clients']), return_exceptions=True)
    mode = getattr(src, 'mode', 'db')
    has_column = bool(getattr(src, 'operator_field', False))
    filtered = bool(period or operator)
    prices = await src.sum_prices() if filtered and hasattr(src, 'sum_prices') else None

    pairs = []
    for row, records in zip(info['clients'], sheets):
        if isinstance(records, LookupError):
            records = []
        elif isinstance(records, BaseException):
            raise records
        pairs.append((row, records))

    # Автор попытки: колонка листа, добавочный в базе или подтверждённая сверка с Mango.
    if mode == 'db':
        matching = callcenter_match.empty('Колл-центр ведётся в базе: автор попытки уже записан добавочным Mango.')
        matching['source'] = 'db'
    elif has_column:
        matching = callcenter_match.empty('В таблице есть колонка «Оператор»: сверка с журналом Mango не нужна.')
        matching['source'] = 'sheet'
    elif calls is not None:
        payload = calls if isinstance(calls, dict) else {'items': calls, 'truncated': False}
        if payload.get('reason'):
            matching = callcenter_match.empty(payload['reason'])
        else:
            matching = callcenter_match.attribute([(row['name'], records) for row, records in pairs],
                                                  payload.get('items') or [], tz,
                                                  truncated=bool(payload.get('truncated')))
    else:
        matching = callcenter_match.empty(NO_OPERATOR)
    matched = bool(matching.get('available'))
    robot = robot or {'available': False, 'reason': NO_ROBOT, 'attempts': 0, 'leads': 0, 'source': ''}

    by_id = {op['id']: op for op in operators or []}
    chosen = None
    if operator:
        chosen = by_id.get(operator)
        if chosen is None:
            raise ValueError('Неизвестный сотрудник.')
    states, operator_available = [], mode == 'db' or has_column or matched or bool(robot.get('available'))
    for op in operators or []:
        available, reason = operator_state(op, mode, has_column, matched, robot)
        item = {'id': op['id'], 'name': op.get('short') or op['name'], 'available': available,
                'reason': reason, 'attempts': 0, 'extension': op.get('extension') or '',
                'source': op.get('source') or '', 'kind': op.get('kind') or 'operator'}
        if op.get('kind') == 'robot':
            item['robot'] = {'attempts': robot.get('attempts', 0), 'leads': robot.get('leads', 0),
                             'source': robot.get('source') or '', 'reason': robot.get('reason') or ''}
        states.append(item)
    if chosen is not None:
        state = next(s for s in states if s['id'] == chosen['id'])
        if not state['available']:
            raise ValueError(state['reason'] or 'Сотрудник не сопоставлен.')
    keep = keep_attempts(start, end, chosen) if filtered else None

    clients, callbacks, sources = [], [], defaultdict(lambda: defaultdict(int))
    days = defaultdict(lambda: defaultdict(int))
    bases = []
    total = defaultdict(int)
    unattributed = undated = 0
    for (row, records) in pairs:
        name = row['name']
        for rec in records:
            for attempt in rec['attempts']:
                if not attempt['result']:
                    continue
                if start is not None and not attempt['date']:
                    undated += 1
                    continue
                if filtered and not keep(attempt):
                    continue
                found = next((s for s in states if s['available'] and
                              attempt_belongs(attempt, by_id[s['id']])), None)
                if found is not None:
                    found['attempts'] += 1
                elif operator_available:
                    unattributed += 1
        p = parse_records(records, keep=keep, prices=prices if mode == 'db' else None)
        k = p['counts']
        clients.append({'name': name, 'since': p['since'], 'leads': k['total'], 'called': k['called'],
                        'reached': k['reached'], 'interested': k['interested'], 'callback': k['callback'],
                        'waiting': p['waiting'], 'not_called': k['not_called'],
                        'sum': p['cost'] if filtered else row['sum']})
        for key in ('called', 'reached', 'interested', 'total'):
            total[key] += k[key]
        total['reached_first'] += p['reached_first']
        if filtered:
            total['sum'] = None if p['cost'] is None else (total['sum'] or 0) + p['cost']
        else:
            total['sum'] += clients[-1]['sum']
        for d in p['days']:
            for key in ('first', 'repeat', 'reached', 'interested'):
                days[d['day']][key] += d.get(key, 0)
        callbacks += [{**c, 'client': name} for c in p['callbacks']]
        for s in p['sources']:
            for key in ('total', 'reached', 'interested'):
                sources[(name, s['name'])][key] += s[key]
        bases += [{**b, 'client': name} for b in p['bases'] if b['date']]
    callbacks.sort(key=lambda x: x['last'], reverse=True)
    sum_available = not filtered or mode == 'db'
    if not sum_available:
        total['sum'] = None  # сумма листа не делится по звонкам: показываем прочерк, а не 0
    operator_reason = '' if operator_available else (matching.get('reason') or NO_OPERATOR)
    return {
        'mode': mode, 'tariffs': info['tariffs'], 'clients': clients, 'total': dict(total),
        'days': [{'day': d, **v} for d, v in sorted(days.items())][-45:],
        'callbacks': callbacks[:150],
        'sources': sorted(({'client': c, 'name': n, **v} for (c, n), v in sources.items()), key=lambda x: -x['total'])[:60],
        'bases': sorted(bases, key=lambda x: x['date'], reverse=True)[:40],
        'filters': {
            'period': period or '', 'operator': (chosen or {}).get('id', ''),
            'periods': [list(p) for p in PERIODS], 'period_label': PERIOD_NAMES.get(period or '', 'за всё время'),
            'start': start.isoformat() if start else '', 'end': end.isoformat() if end else '',
            'operator_available': operator_available, 'operator_reason': operator_reason,
            'operators': states, 'unattributed': unattributed, 'undated': undated,
            'sum_available': sum_available, 'sum_reason': '' if sum_available else NO_SUM,
            'matching': matching, 'robot': robot,
        },
    }
