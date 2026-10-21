"""Сводка отдела, журнал звонков и общий список перезвонов для CRM.

Всё считается из базы, без Google Таблиц и без модели:
- наборы и время на линии из calls (Mango);
- дозвон — исходящий разговор от 10 секунд, кроме тех, что бот разобрал как
  автоответчик или сброс (решение Григория от 21.10: одинаково везде);
- разговоры с результатом, встречи и КП, потери с шансом и частые ошибки из call_reviews.

Менеджеры Сводки — продажники отдела (clients.sales_extensions). Ольга работает
по своему скрипту и видна в «Колл-центре», робот — в «Роботе».
"""
from __future__ import annotations

import calendar
import collections
import json
from datetime import date, datetime, time, timedelta, timezone

from review import engine, kitchen as review_kitchen
from review.render import CATEGORY_LABELS, OUTCOME_LABELS
from tools import Forbidden, is_privileged
from . import store

LIVE_SECONDS = 10
NO_TALK = 'no_conversation'
REVIEWED = sorted(engine.REVIEWED)
PERIODS = ('today', '7', '30', 'month')
HOURS = range(9, 20)
HEAT_DAYS = 30
JOURNAL_PAGE = 50
KIND_NAMES = {'cold_first': 'холодный', 'repeat': 'повторный', 'gatekeeper_only': 'секретарь'}
RESULTS = ('result', 'lost', 'hot', 'none', 'no_answer')


def loads(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def shift_month(day: date, months: int) -> date:
    y, m = divmod(day.year * 12 + day.month - 1 + months, 12)
    return date(y, m + 1, min(day.day, calendar.monthrange(y, m + 1)[1]))


def bounds(period: str, now: datetime, tz) -> dict:
    """Начало и конец периода и такой же отрезок перед ним, чтобы стрелки сравнивали равное с равным."""
    local = now.astimezone(tz)
    today = local.date()
    midnight = lambda d: datetime.combine(d, time(0), tzinfo=tz)  # noqa: E731
    if period == 'today':
        start = midnight(today)
        return {'start': start, 'end': now, 'prev_start': start - timedelta(days=1), 'prev_end': now - timedelta(days=1),
                'vs': 'к вчера в это же время', 'hourly': True}
    if period == 'month':
        start = midnight(today.replace(day=1))
        prev_first = shift_month(today.replace(day=1), -1)
        prev_end = datetime.combine(shift_month(today, -1), local.timetz().replace(tzinfo=None), tzinfo=tz)
        return {'start': start, 'end': now, 'prev_start': midnight(prev_first), 'prev_end': prev_end,
                'vs': f'к 1–{shift_month(today, -1).day} прошлого месяца', 'hourly': False}
    days = int(period)
    start = midnight(today - timedelta(days=days - 1))
    return {'start': start, 'end': now, 'prev_start': start - timedelta(days=days), 'prev_end': start,
            'vs': f'к прошлым {days} дням', 'hourly': False}


async def sales_team(pool, client_id: int) -> list[dict]:
    """Продажники отдела: из sales_extensions, иначе все менеджеры кроме кухонного скрипта."""
    people = await store.team(pool, client_id)
    sales = await pool.fetchval("SELECT to_jsonb(c)->'sales_extensions' FROM clients c WHERE id=$1", client_id)
    sales = loads(sales) if sales else None
    if isinstance(sales, list) and sales:
        keep = {str(x) for x in sales}
        return [p for p in people if p['ext'] in keep]
    return [p for p in people if not review_kitchen.handles(p['ext'])]


def blank() -> dict:
    return {'dials': 0, 'live': 0, 'seconds': 0, 'talks': 0, 'result': 0, 'meet': 0, 'lost': 0, 'categories': {}}


def add(acc: dict, r) -> None:
    out = r['direction'] == store.OUTGOING
    seconds = r['seconds']
    acc['seconds'] += seconds
    if not out:
        return
    acc['dials'] += 1
    if seconds >= LIVE_SECONDS and r['kind'] != NO_TALK:
        acc['live'] += 1
    if r['kind'] not in engine.REVIEWED:
        return
    acc['talks'] += 1
    if r['success']:
        acc['result'] += 1
        if {'meeting', 'proposal'} & set(loads(r['outcome']) or []):
            acc['meet'] += 1
    elif r['chance'] in ('hot', 'normal'):
        acc['lost'] += 1
        if r['category']:
            acc['categories'][r['category']] = acc['categories'].get(r['category'], 0) + 1


def top_mistake(categories: dict) -> dict | None:
    if not categories:
        return None
    key, n = sorted(categories.items(), key=lambda kv: (-kv[1], kv[0] == 'other', kv[0]))[0]
    return {'key': key, 'label': CATEGORY_LABELS.get(key, key), 'n': n}


async def calls_between(pool, client_id: int, start: datetime, end: datetime, exts: list[str]):
    return await pool.fetch(
        """SELECT c.id, c.extension, c.direction, c.call_started_at, c.client_number,
                  coalesce(c.duration_seconds, 0) AS seconds, r.kind, r.success, r.chance, r.category,
                  r.data->'outcome' AS outcome
           FROM calls c LEFT JOIN call_reviews r ON r.call_id=c.id
           WHERE c.client_id=$1 AND c.call_started_at >= $2 AND c.call_started_at < $3
             AND (c.extension = ANY($4::text[]) OR (c.direction=1 AND c.extension IS NULL))""",
        client_id, start, end, exts)


async def missed_incoming(pool, client_id: int, rows, exts: set[str], tz, names: dict, common: bool) -> list[dict]:
    """Пропущенные входящие, по которым после этого никто не поговорил с клиентом.

    common: показывать и звонки на общий номер без добавочного (когда смотрим весь отдел).
    """
    missed = [r for r in rows if r['direction'] == store.INCOMING and not r['seconds'] and r['client_number']
              and (r['extension'] in exts or (common and r['extension'] is None))]
    if not missed:
        return []
    later = await pool.fetch(
        """SELECT client_number, max(call_started_at) AS at FROM calls
           WHERE client_id=$1 AND client_number = ANY($2::text[]) AND coalesce(duration_seconds,0) > 0
           GROUP BY client_number""", client_id, list({r['client_number'] for r in missed}))
    talked = {r['client_number']: r['at'] for r in later}
    seen, out = set(), []
    for r in sorted(missed, key=lambda x: x['call_started_at'], reverse=True):
        number = r['client_number']
        if number in seen or (number in talked and talked[number] > r['call_started_at']):
            continue
        seen.add(number)
        out.append({'number': number, 'at': store.fmt_dt(r['call_started_at'], tz),
                    'who': names.get(r['extension']) or ''})
    return out


async def summary(pool, actor, period: str = '7', ext: str | None = None, now: datetime | None = None) -> dict:
    if period not in PERIODS:
        raise ValueError('Неизвестный период.')
    tz = await store.zone(pool, actor.client_id)
    now = now or datetime.now(timezone.utc)
    team = await sales_team(pool, actor.client_id)
    if not is_privileged(actor):
        ext = actor.extension
        team = [p for p in team if p['ext'] == ext] or [{'ext': ext, 'name': ''}]
    elif ext:
        team = [p for p in team if p['ext'] == ext]
        if not team:
            raise Forbidden('Такого менеджера нет.')
    exts = [p['ext'] for p in team]
    names = {p['ext']: p['name'] for p in await store.team(pool, actor.client_id)}
    b = bounds(period, now, tz)
    heat_start = datetime.combine(now.astimezone(tz).date() - timedelta(days=HEAT_DAYS - 1), time(0), tzinfo=tz)
    rows = await calls_between(pool, actor.client_id, min(b['prev_start'], heat_start), now, exts)

    cur, prev = blank(), blank()
    per = {e: blank() for e in exts}
    series: dict[str, dict] = {}
    if b['hourly']:
        for h in HOURS:
            series[f'{h:02d}'] = {'key': f'{h:02d}', 'dials': 0, 'result': 0, 'live': 0}
    else:
        d = b['start'].date()
        while d <= now.astimezone(tz).date():
            series[d.isoformat()] = {'key': d.isoformat(), 'dials': 0, 'result': 0, 'live': 0}
            d += timedelta(days=1)
    heat = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        at = r['call_started_at']
        local = at.astimezone(tz)
        if at >= heat_start and r['direction'] == store.OUTGOING and r['extension'] in per:
            cell = heat[(local.isoweekday(), local.hour)]
            cell[0] += 1
            cell[1] += r['seconds'] >= LIVE_SECONDS and r['kind'] != NO_TALK
        if r['extension'] not in per:
            continue
        if b['start'] <= at < b['end']:
            add(cur, r)
            add(per[r['extension']], r)
            key = f'{local.hour:02d}' if b['hourly'] else local.date().isoformat()
            if key in series:
                one = blank()
                add(one, r)
                for k in ('dials', 'result', 'live'):
                    series[key][k] += one[k]
        elif b['prev_start'] <= at < b['prev_end']:
            add(prev, r)
    managers = [{'ext': p['ext'], 'name': p['name'], **{k: v for k, v in per[p['ext']].items() if k != 'categories'},
                 'mistake': top_mistake(per[p['ext']]['categories'])} for p in team]
    current = [r for r in rows if b['start'] <= r['call_started_at'] < b['end']]
    return {
        'period': period, 'vs': b['vs'], 'hourly': b['hourly'], 'today': now.astimezone(tz).date().isoformat(),
        'now': now.astimezone(tz).strftime('%H:%M'), 'live_seconds': LIVE_SECONDS,
        'cur': {k: v for k, v in cur.items() if k != 'categories'},
        'prev': {k: v for k, v in prev.items() if k != 'categories'},
        'series': list(series.values()),
        'managers': managers,
        'missed': await missed_incoming(pool, actor.client_id, current, set(exts), tz, names, common=not ext),
        'heat': [{'wd': wd, 'hour': h, 'dials': v[0], 'live': v[1]} for (wd, h), v in sorted(heat.items()) if h in HOURS],
        'heat_days': HEAT_DAYS,
    }


def call_entry(r, tz, names: dict) -> dict:
    data = loads(r['data']) if r['data'] is not None else {}
    reviewed = r['kind'] in engine.REVIEWED
    moment = data.get('key_moment') if isinstance(data.get('key_moment'), dict) else {}
    client = data.get('client') if isinstance(data.get('client'), dict) else {}
    if not r['seconds']:
        result, text = 'no_answer', ''
    elif not reviewed:
        result, text = 'none', ''
    elif r['success']:
        result = 'result'
        text = ', '.join(OUTCOME_LABELS.get(o, o) for o in data.get('outcome') or [])
    else:
        result = 'hot' if r['chance'] == 'hot' else 'lost' if r['chance'] == 'normal' else 'none'
        text = CATEGORY_LABELS.get(r['category'], '') if r['category'] else ''
    return {'id': r['id'], 'at': store.fmt_dt(r['call_started_at'], tz), 'ext': r['extension'],
            'manager': names.get(r['extension']) or (f"Доб. {r['extension']}" if r['extension'] else ''),
            'number': r['client_number'], 'company': client.get('company') or r['task_company'] or '',
            'incoming': r['direction'] == store.INCOMING, 'seconds': r['seconds'], 'rec': r['rec'],
            'reviewed': reviewed, 'kind': KIND_NAMES.get(r['kind'], ''), 'result': result, 'text': text,
            'line': data.get('rop_line') or moment.get('what_happened') or ''}


async def journal(pool, actor, period: str = '7', ext: str | None = None, result: str = '', kind: str = '',
                  q: str = '', offset: int = 0, now: datetime | None = None) -> dict:
    """Журнал звонков: менеджер видит свои, директор всех, вместе со входящими на общий номер.

    Подгружается страницами по 50.
    """
    if period not in PERIODS:
        raise ValueError('Неизвестный период.')
    if result and result not in RESULTS:
        raise ValueError('Неизвестный итог.')
    if kind and kind not in KIND_NAMES:
        raise ValueError('Неизвестный тип звонка.')
    tz = await store.zone(pool, actor.client_id)
    now = now or datetime.now(timezone.utc)
    names = {p['ext']: p['name'] for p in await store.team(pool, actor.client_id)}
    if not is_privileged(actor):
        exts = [actor.extension]
    elif ext:
        if ext not in names:
            raise Forbidden('Такого менеджера нет.')
        exts = [ext]
    else:
        exts = list(names)
    b = bounds(period, now, tz)
    digits = ''.join(ch for ch in q if ch.isdigit())
    if len(digits) == 11 and digits[0] == '8':
        digits = '7' + digits[1:]
    text = q.strip().lower()
    talk = 'r.kind IN (' + ','.join(f"'{k}'" for k in REVIEWED) + ')'
    where = {
        'result': 'r.success', 'lost': "NOT r.success AND r.chance='normal'", 'hot': "NOT r.success AND r.chance='hot'",
        'none': f"c.duration_seconds > 0 AND (r.call_id IS NULL OR NOT {talk} OR (NOT r.success AND r.chance='none'))",
        'no_answer': 'coalesce(c.duration_seconds,0)=0',
    }.get(result, 'true')
    if result in ('result', 'lost', 'hot'):
        where += f' AND {talk}'
    rows = await pool.fetch(
        f"""SELECT c.id, c.extension, c.direction, c.call_started_at, c.client_number,
                   coalesce(c.duration_seconds, 0) AS seconds, c.recording_id IS NOT NULL AS rec,
                   r.kind, r.success, r.chance, r.category, r.data,
                   (SELECT t.company FROM callback_tasks t WHERE t.client_id=c.client_id AND t.client_number=c.client_number
                      AND t.company IS NOT NULL ORDER BY t.id DESC LIMIT 1) AS task_company
            FROM calls c LEFT JOIN call_reviews r ON r.call_id=c.id
            WHERE c.client_id=$1 AND c.call_started_at >= $2 AND c.call_started_at < $3
              AND (c.extension = ANY($4::text[]) OR ($10 AND c.extension IS NULL AND c.direction=1))
              AND ({where})
              AND ($5 = '' OR r.kind = $5)
              AND (($6 = '' AND $7 = '') OR ($6 <> '' AND c.client_number LIKE '%' || $6 || '%')
                   OR ($7 <> '' AND lower(coalesce(r.data->'client'->>'company', '')) LIKE '%' || $7 || '%'))
            ORDER BY c.call_started_at DESC LIMIT $8 OFFSET $9""",
        actor.client_id, b['start'], b['end'], exts, kind, digits, text, JOURNAL_PAGE + 1, max(0, offset),
        is_privileged(actor) and not ext)
    items = [call_entry(r, tz, names) for r in rows[:JOURNAL_PAGE]]
    return {'items': items, 'more': len(rows) > JOURNAL_PAGE, 'offset': offset + len(items),
            'kinds': KIND_NAMES, 'results': {'result': 'С результатом', 'hot': 'Горячий, потерян', 'lost': 'Потерян, шанс был',
                                             'none': 'Без шанса', 'no_answer': 'Не дозвонились'}}


async def callbacks(pool, actor, ext: str | None = None) -> dict:
    """Один список перезвонов и встреч: договорённости из разговоров и отметки в CRM.

    Бот сам ставит перезвон из разговора (review/callbacks.py), CRM — из итога звонка. Один
    клиент может попасть в список дважды; показываем ближайшую договорённость по номеру.
    """
    tz = await store.zone(pool, actor.client_id)
    today = datetime.now(tz).date()
    names = {p['ext']: p['name'] for p in await store.team(pool, actor.client_id)}
    if not is_privileged(actor):
        exts = [actor.extension]
    elif ext:
        if ext not in names:
            raise Forbidden('Такого менеджера нет.')
        exts = [ext]
    else:
        exts = list(names)
    rows = await pool.fetch(
        """SELECT t.*, (SELECT l.name FROM crm_leads l WHERE l.client_id=t.client_id AND l.extension=t.extension
                         AND l.number=t.client_number LIMIT 1) AS lead_name
           FROM callback_tasks t WHERE t.client_id=$1 AND t.extension = ANY($2::text[]) AND t.status='open'
           ORDER BY t.due_date, t.due_time NULLS LAST, t.id LIMIT 1000""", actor.client_id, exts)
    seen, items = {}, []
    for t in rows:
        key = t['client_number'] or f"id{t['id']}"
        if key in seen:
            seen[key]['more'] += 1
            continue
        item = {**store.task_item(t, today), 'ext': t['extension'], 'manager': names.get(t['extension']) or '', 'more': 0}
        seen[key] = item
        items.append(item)
    return {'items': items, 'today': today.isoformat()}
