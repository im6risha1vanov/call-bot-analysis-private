"""Карточка сотрудника в CRM: всё о человеке в одном месте.

Цифры считаются из уже сохранённых данных, модель не вызывается:
- наборы и дозвоны из calls (Mango);
- разговоры, результаты и причины потерь из call_reviews (разбор v2);
- перезвоны из callback_tasks, тренировки из training_sessions, доступы из employees и crm_invites.

Директор видит любого сотрудника, менеджер только себя. Звонки Ольги идут по
своему скрипту (review/kitchen.py), разборов v2 у неё нет, поэтому разделы с
разборами для неё пустые и карточка это говорит.
"""
from __future__ import annotations

import collections
from datetime import datetime, timedelta

from review import memory, render
from review import kitchen as review_kitchen
from tools import Forbidden, is_privileged
from . import staff, store, training

DAYS = memory.MONTH
EXAMPLES = 2


async def dial_rows(pool, client_id: int, since: datetime, ext: str | None = None):
    min_seconds = await pool.fetchval('SELECT min_call_seconds FROM clients WHERE id=$1', client_id) or 30
    rows = await pool.fetch(
        """SELECT extension, call_started_at, coalesce(duration_seconds,0) AS seconds FROM calls
           WHERE client_id=$1 AND call_started_at >= $2 AND direction=2 AND extension IS NOT NULL
             AND ($3::text IS NULL OR extension=$3)""", client_id, since, ext)
    return rows, min_seconds


def dials(rows, min_seconds: int, since: datetime, until: datetime | None = None) -> dict:
    out = {'dials': 0, 'answered': 0, 'talks': 0}
    for r in rows:
        if r['call_started_at'] < since or (until and r['call_started_at'] >= until):
            continue
        out['dials'] += 1
        out['answered'] += r['seconds'] > 0
        out['talks'] += r['seconds'] >= min_seconds
    return out


async def summary(pool, client_id: int, now: datetime | None = None) -> dict[str, dict]:
    """Строка списка «Сотрудники»: неделя к прошлой неделе и частая ошибка за 30 дней."""
    _, _, week, prev, _ = await memory.bounds(pool, client_id, now)
    stats = await memory.periods(pool, client_id, now)
    calls, min_seconds = await dial_rows(pool, client_id, prev)
    by_ext = collections.defaultdict(list)
    for r in calls:
        by_ext[r['extension']].append(r)
    result = {}
    for ext in set(stats) | set(by_ext):
        acc = stats.get(ext) or {'week': memory.blank(), 'prev': memory.blank(), 'month': memory.blank()}
        result[ext] = {'week': acc['week'], 'prev': acc['prev'], 'top': memory.top_category(acc['month']['categories']),
                       'dials': dials(by_ext[ext], min_seconds, week)}
    return result


def call_item(r, tz) -> dict:
    data = memory.loads(r['data']) if 'data' in r.keys() else {}
    moment = data.get('key_moment') if isinstance(data.get('key_moment'), dict) else {}
    if r['success']:
        verdict = ', '.join(render.OUTCOME_LABELS.get(o, o) for o in memory.loads(r['outcome']) or [])
    else:
        verdict = render.CATEGORY_LABELS.get(r['category'], '') if r['category'] else ''
    return {'id': r['id'], 'at': store.fmt_dt(r['call_started_at'], tz), 'number': r['client_number'],
            'seconds': r['duration_seconds'] or 0, 'rec': r['rec'], 'success': bool(r['success']), 'chance': r['chance'],
            'category': r['category'], 'verdict': verdict, 'what': moment.get('what_happened'),
            'rop_line': data.get('rop_line')}


async def target(pool, actor, ext) -> dict:
    """Чью карточку открываем. Менеджеру всегда свою."""
    if actor is None:
        raise Forbidden('Нужно войти.')
    if not is_privileged(actor):
        ext = actor.extension
    return await staff._person(pool, actor.client_id, ext)


async def card(pool, actor, ext, now: datetime | None = None) -> dict:
    person = await target(pool, actor, ext)
    ext = person['ext']
    privileged = is_privileged(actor)
    tz, today, week, prev, month = await memory.bounds(pool, actor.client_id, now)
    out = {'ext': ext, 'name': person['name'] or (f'Доб. {ext}' if ext.isdigit() else ext), 'role': person['role'],
           'role_title': staff.ROLE_TITLES.get(person['role'], person['role']), 'today': today.isoformat(),
           'kitchen': review_kitchen.handles(ext), 'manager': person['role'] == 'manager'}
    if privileged:
        out['access'] = await access(pool, actor, person)
    if not out['manager']:
        return out

    calls, min_seconds = await dial_rows(pool, actor.client_id, month, ext)
    rows = await memory.reviewed_rows(pool, actor.client_id, month, ext, with_data=True)
    periods = {'week': memory.blank(), 'prev': memory.blank(), 'month': memory.blank()}
    days = {(today - timedelta(days=i)).isoformat(): {'dials': 0, 'talks': 0, 'success': 0} for i in range(DAYS)}
    examples = collections.defaultdict(list)
    positives = []
    for r in rows:
        memory.add(periods['month'], r)
        if r['call_started_at'] >= week:
            memory.add(periods['week'], r)
        elif r['call_started_at'] >= prev:
            memory.add(periods['prev'], r)
        day = days.get(r['call_started_at'].astimezone(tz).date().isoformat())
        if day:
            day['talks'] += 1
            day['success'] += bool(r['success'])
        data = memory.loads(r['data'])
        if r['category'] and not r['success'] and len(examples[r['category']]) < EXAMPLES:
            examples[r['category']].append(call_item(r, tz))
        for p in data.get('positives') or []:
            if isinstance(p, dict) and p.get('why') and len(positives) < 6:
                positives.append({'id': r['id'], 'at': store.fmt_dt(r['call_started_at'], tz), 'why': p['why']})
    for r in calls:
        day = days.get(r['call_started_at'].astimezone(tz).date().isoformat())
        if day:
            day['dials'] += 1
    week_cats, prev_cats = periods['week']['categories'], periods['prev']['categories']
    mistakes = memory.ranked(periods['month']['categories'])
    out.update({
        'periods': periods,
        'dials': {'week': dials(calls, min_seconds, week), 'prev': dials(calls, min_seconds, prev, week),
                  'month': dials(calls, min_seconds, month)},
        'min_seconds': min_seconds,
        'days': [{'day': d, **v} for d, v in sorted(days.items())],
        'mistakes': [{'key': k, 'label': render.CATEGORY_LABELS.get(k, k), 'month': n, 'week': week_cats.get(k, 0),
                      'prev': prev_cats.get(k, 0), 'examples': examples[k]} for k, n in mistakes],
        'positives': positives,
        'outcome_labels': render.OUTCOME_LABELS,
        'calls': [call_item(r, tz) for r in rows[:60]],
        'tasks': (await store.tasks(pool, actor.client_id, ext))['items'][:50],
        'trainings': await training.listing(pool, actor, ext),
    })
    return out


async def access(pool, actor, person: dict) -> dict:
    """Раздел «Доступ и бот»: то же, что строка прежней таблицы сотрудников, плюс личные ссылки в CRM."""
    item = next(i for i in (await staff.listing(pool, actor))['items'] if i['ext'] == person['ext'])
    links = await pool.fetch(
        """SELECT role, last_used_at FROM crm_invites WHERE client_id=$1 AND revoked_at IS NULL
             AND (extension=$2 OR ($3::bigint IS NOT NULL AND telegram_user_id=$3))""",
        actor.client_id, person['ext'], person['tg'])
    used = max((r['last_used_at'] for r in links if r['last_used_at']), default=None)
    return {**item, 'crm_links': len(links), 'crm_used': used.isoformat() if used else None}
