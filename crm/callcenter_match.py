"""Сверка звонков операторов из Mango с листом «Прозвон лидов»: только подтверждённые связи.

Лист хранит лида, результат и дату попытки, но не автора звонка. Автор берётся из
журнала Mango (``calls``): добавочный, номер клиента и время начала разговора.
Связь попытки с звонком ставится только тогда, когда сходятся все подтверждённые
признаки:

* нормализованный телефон — последние 10 цифр, поэтому ``+7 (999) …``,
  ``8 999 …`` и число из Google Sheets (``7,98E+10``) дают один ключ;
* календарный день звонка по часовому поясу компании — дата попытки в листе;
* компания/кампания — лист клиента колл-центра; если в записи звонка есть метка
  кампании, она должна совпасть с названием листа, проектом или тегом.

Что не подтвердилось, не связывается и не превращается в лид: неоднозначности
(один телефон и день в нескольких листах), повторные звонки сверх попыток листа,
попытки без звонка и звонки без лида возвращаются отдельными числами и
примерами. Телефоны в примерах замаскированы: отчёт не копирует базу клиентов.
"""
from __future__ import annotations

import re
from collections import deque
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MAX_CALLS = 5000  # больше в один ответ не берём: сверка честно помечается ограниченной
SAMPLES = 5
SCI = re.compile(r'^-?\d+(?:[.,]\d+)?[eE][+-]?\d+$')


def digits(value) -> str:
    """Цифры телефона. Google Sheets может отдать число как «7,98E+10»."""
    text = str(value or '').strip().replace('\u00a0', ' ')
    if SCI.match(text):
        try:
            text = str(int(float(text.replace(',', '.'))))
        except (ValueError, OverflowError):
            return ''
    return re.sub(r'\D', '', text)


def phone_key(value) -> str:
    """Ключ телефона — последние 10 цифр; короче — не телефон, связи нет."""
    found = digits(value)
    return found[-10:] if len(found) >= 10 else ''


def mask(value) -> str:
    key = phone_key(value)
    return f'+7 *** ** {key[-2:]}' if key else '—'


def day_of(value, tz: ZoneInfo) -> str:
    """Календарный день звонка по часовому поясу компании (ISO)."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(tz).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return ''


def _field(item, *names):
    for name in names:
        try:
            value = item[name]
        except (KeyError, TypeError):
            value = getattr(item, name, None)
        if value not in (None, ''):
            return value
    return None


def call_view(call) -> dict:
    """Звонок журнала Mango в одном виде: телефон, время, добавочный, кампания."""
    return {'id': _field(call, 'id', 'call_id'), 'phone': _field(call, 'phone', 'client_number') or '',
            'at': _field(call, 'at', 'call_started_at'), 'extension': str(_field(call, 'extension', 'ext') or ''),
            'campaign': str(_field(call, 'campaign', 'company', 'project') or '')}


def _sample(call: dict, day: str, *, note='', campaign='') -> dict:
    return {'phone': mask(call.get('phone')), 'date': day, 'extension': call.get('extension') or '',
            'campaign': campaign or call.get('campaign') or '', 'note': note}


def empty(answer: str = '') -> dict:
    return {'available': False, 'reason': answer, 'source': '', 'calls': 0, 'linked': 0, 'ambiguous': 0,
            'repeated': 0, 'unmatched': 0, 'campaign_mismatch': 0, 'missing': {'phone': 0, 'date': 0},
            'attempts_without_call': None, 'by_extension': {},
            'samples': {'ambiguous': [], 'repeated': [], 'unmatched': [], 'missing_phone': [],
                        'missing_date': [], 'campaign_mismatch': []},
            'truncated': False}


def _ext(report: dict, extension: str) -> dict:
    return report['by_extension'].setdefault(extension or '', {
        'linked': 0, 'ambiguous': 0, 'repeated': 0, 'unmatched': 0, 'campaign_mismatch': 0,
        'missing_phone': 0, 'missing_date': 0})


def _keep(report: dict, bucket: str, sample: dict) -> None:
    samples = report['samples'][bucket]
    if len(samples) < SAMPLES:
        samples.append(sample)


def attribute(pairs, calls, tz: ZoneInfo | None = None, *, truncated: bool = False) -> dict:
    """Связать звонки с попытками и записать добавочный только в подтверждённые попытки.

    pairs — [(название листа клиента, записи лида)], records из cabinet.sheet_records.
    Попытка получает ``by`` = добавочный Mango только при однозначной связи; иначе
    автор остаётся пустым и попытка видна в отдельном счёте, а не у оператора.
    """
    tz = tz or ZoneInfo('Europe/Moscow')
    report = empty()
    report.update(available=True, source='mango', attempts_without_call=0, truncated=bool(truncated))
    groups: dict[tuple[str, str], dict] = {}
    for client, records in pairs:
        for record in records:
            for index, attempt in enumerate(record.get('attempts') or []):
                result = str(attempt.get('result') or '')
                if not result:
                    continue
                day = str(attempt.get('date') or '')
                key = phone_key(record.get('phone'))
                if not key:
                    report['missing']['phone'] += 1
                    _keep(report, 'missing_phone',
                          _sample({'phone': record.get('phone')}, day, note='в строке лида нет телефона'))
                    continue
                if not day:
                    report['missing']['date'] += 1
                    _keep(report, 'missing_date',
                          _sample({'phone': record.get('phone')}, '', note='в попытке нет даты звонка'))
                    continue
                groups.setdefault((key, day), {'calls': [], 'attempts': []})['attempts'].append(
                    (client, record, index))

    for raw in calls or []:
        call = call_view(raw)
        report['calls'] += 1
        extension = _ext(report, call['extension'])
        if not phone_key(call['phone']):
            report['missing']['phone'] += 1
            extension['missing_phone'] += 1
            _keep(report, 'missing_phone', _sample(call, ''))
            continue
        day = day_of(call['at'], tz)
        if not day:
            report['missing']['date'] += 1
            extension['missing_date'] += 1
            _keep(report, 'missing_date', _sample(call, ''))
            continue
        groups.setdefault((phone_key(call['phone']), day), {'calls': [], 'attempts': []})['calls'].append(
            (call, day))

    for (key, day), group in groups.items():
        ordered_calls = sorted(group['calls'], key=lambda item: (item[0]['at'], str(item[0]['id'])))
        attempts = group['attempts']
        if not ordered_calls:
            if report['attempts_without_call'] is not None:
                report['attempts_without_call'] += len([a for a in attempts if not str(a[1]['attempts'][a[2]]
                                                                                        .get('by'))])
            continue
        if not attempts:
            for call, _ in ordered_calls:
                report['unmatched'] += 1
                _ext(report, call['extension'])['unmatched'] += 1
                _keep(report, 'unmatched', _sample(call, day))
            continue
        leads = {id(record) for _, record, _ in attempts}
        if len(leads) > 1:
            where = ', '.join(sorted({str(client) for client, _, _ in attempts}))[:80]
            for call, _ in ordered_calls:
                report['ambiguous'] += 1
                _ext(report, call['extension'])['ambiguous'] += 1
                _keep(report, 'ambiguous', _sample(call, day, note=f'телефон и день в {len(leads)} лидах: {where}'))
            continue
        names = set()
        for client, record, _ in attempts:
            names |= {str(client or '').strip().lower(), str(record.get('tag') or '').strip().lower(),
                      str(record.get('project') or '').strip().lower()}
        names.discard('')
        queue = deque(item for item in attempts
                      if not str(item[1]['attempts'][item[2]].get('by')))
        for call, _ in ordered_calls:
            if call['campaign'] and call['campaign'].strip().lower() not in names:
                report['campaign_mismatch'] += 1
                _ext(report, call['extension'])['campaign_mismatch'] += 1
                _keep(report, 'campaign_mismatch',
                      _sample(call, day, note='метка кампании звонка не совпала с листом, проектом или тегом'))
                continue
            if not queue:
                report['repeated'] += 1
                _ext(report, call['extension'])['repeated'] += 1
                _keep(report, 'repeated', _sample(call, day, note='звонок сверх попыток, записанных в листе'))
                continue
            client, record, index = queue.popleft()
            record['attempts'][index]['by'] = call['extension']
            report['linked'] += 1
            _ext(report, call['extension'])['linked'] += 1
        if report['attempts_without_call'] is not None:
            report['attempts_without_call'] += len(queue)
    if report['truncated']:
        report['attempts_without_call'] = None  # журнал неполный: «без звонка» утверждать нельзя
    return report


async def mango_counts(pool, client_id: int, extensions, start: date | None, end: date | None, tz: ZoneInfo) -> dict:
    """Count all outgoing calls in the CRM journal sourced from Mango, independently of sheet matching."""
    extensions = [str(e) for e in extensions or [] if e]
    if not extensions:
        return {}
    start_at = datetime.combine(start, time.min, tz) if start else None
    end_at = datetime.combine(end + timedelta(days=1), time.min, tz) if end else None
    rows = await pool.fetch(
        """SELECT extension, count(*) AS n FROM calls
           WHERE client_id=$1 AND direction=2 AND extension = ANY($2::text[])
             AND ($3::timestamptz IS NULL OR call_started_at >= $3)
             AND ($4::timestamptz IS NULL OR call_started_at < $4)
           GROUP BY extension""", client_id, extensions, start_at, end_at)
    return {str(row['extension']): int(row['n']) for row in rows}


async def mango_calls(pool, client_id: int, extensions, start: date | None, end: date | None,
                      tz: ZoneInfo, limit: int = MAX_CALLS) -> dict:
    """Исходящие звонки операторов из журнала Mango за период (по дню компании).

    Возвращает {'items': [...], 'truncated': bool} или {'items': [], 'truncated': False,
    'reason': ...}, если журнала нет. Ничего не пишет.
    """
    extensions = [str(e) for e in extensions or [] if str(e)]
    if not extensions:
        return {'items': [], 'truncated': False,
                'reason': 'Добавочные операторов не подтверждены: сверять звонки не с чем.'}
    start_at = datetime.combine(start, time.min, tz) if start else None
    end_at = datetime.combine(end + timedelta(days=1), time.min, tz) if end else None
    try:
        rows = await pool.fetch(
            """SELECT id, extension, client_number, call_started_at FROM calls
               WHERE client_id=$1 AND direction=2 AND extension = ANY($2::text[])
                 AND ($3::timestamptz IS NULL OR call_started_at >= $3)
                 AND ($4::timestamptz IS NULL OR call_started_at < $4)
               ORDER BY call_started_at DESC, id DESC LIMIT $5""",
            client_id, extensions, start_at, end_at, int(limit) + 1)
    except Exception as exc:  # noqa: BLE001 — журнала может не быть: это состояние, а не догадка
        text = str(exc).lower()
        if not any(marker in text for marker in ('does not exist', 'undefinedtable', 'undefinedcolumn', 'relation')):
            raise
        return {'items': [], 'truncated': False,
                'reason': 'Журнал звонков Mango в базе не прочитан: сверка с листом недоступна.'}
    truncated = len(rows) > int(limit)
    items = [{'id': row['id'], 'extension': row['extension'], 'phone': row['client_number'],
              'at': row['call_started_at']} for row in list(rows)[:int(limit)]]
    items.sort(key=lambda item: (item['at'], str(item['id'])))
    return {'items': items, 'truncated': truncated}
