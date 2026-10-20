"""Память по менеджерам, слой 1: цифры из сохранённых разборов, без модели.

По каждому менеджеру за 7 дней, предыдущие 7 дней и 30 дней: сколько
разговоров разобрано, сколько с результатом и каким, сколько потеряно с
шансом и на каком месте (key_moment.category). Этим пользуются карточка
сотрудника в CRM (crm/people.py) и лист «Память менеджеров» в Google-таблице
(review/sheets.py). Ольга (KITCHEN_EXTENSIONS) идёт по своему скрипту и в
память не входит.
"""
from __future__ import annotations

import collections
import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import engine
from .render import CATEGORY_LABELS

REVIEWED = sorted(engine.REVIEWED)
MONTH = 30


def loads(value):
    if not value:
        return {}
    return json.loads(value) if isinstance(value, str) else value


def blank() -> dict:
    return {'talks': 0, 'success': 0, 'hot': 0, 'lost': 0, 'outcomes': {}, 'categories': {}}


def add(acc: dict, row) -> None:
    """row: success, chance, category, outcome (список из data)."""
    acc['talks'] += 1
    if row['success']:
        acc['success'] += 1
        for o in loads(row['outcome']) or []:
            acc['outcomes'][o] = acc['outcomes'].get(o, 0) + 1
        return
    if row['chance'] in ('hot', 'normal'):
        acc['lost'] += 1
        acc['hot'] += row['chance'] == 'hot'
        if row['category']:
            acc['categories'][row['category']] = acc['categories'].get(row['category'], 0) + 1


def ranked(categories: dict) -> list[tuple[str, int]]:
    """Причины потерь от частых к редким; «другое» при равенстве в конце."""
    return sorted(categories.items(), key=lambda kv: (-kv[1], kv[0] == 'other', kv[0]))


def top_category(categories: dict) -> dict | None:
    if not categories:
        return None
    key, n = ranked(categories)[0]
    return {'key': key, 'label': CATEGORY_LABELS.get(key, key), 'n': n}


def share(acc: dict) -> int | None:
    return round(100 * acc['success'] / acc['talks']) if acc['talks'] else None


async def bounds(pool, client_id: int, now: datetime | None = None):
    """Часовой пояс, сегодня и начала периодов в поясе отдела: 7 дней, прошлые 7 дней, 30 дней."""
    zone = await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', client_id)
    tz = ZoneInfo(zone or 'Europe/Moscow')
    today = (now or datetime.now(timezone.utc)).astimezone(tz).date()
    start = lambda days: datetime.combine(today - timedelta(days=days - 1), time(0), tzinfo=tz)  # noqa: E731
    return tz, today, start(7), start(14), start(MONTH)


async def reviewed_rows(pool, client_id: int, since: datetime, ext: str | None = None, with_data: bool = False):
    return await pool.fetch(
        f"""SELECT c.id, c.extension, c.call_started_at, c.client_number, c.duration_seconds,
                   c.recording_id IS NOT NULL AS rec, r.success, r.chance, r.category, r.data->'outcome' AS outcome
                   {', r.data' if with_data else ''}
            FROM call_reviews r JOIN calls c ON c.id=r.call_id
            WHERE r.client_id=$1 AND c.call_started_at >= $2 AND r.kind = ANY($3::text[])
              AND ($4::text IS NULL OR c.extension=$4)
            ORDER BY c.call_started_at DESC""", client_id, since, REVIEWED, ext)


async def periods(pool, client_id: int, now: datetime | None = None) -> dict[str, dict]:
    """{добавочный: {'week', 'prev', 'month', 'example': {причина: id последнего звонка}}}."""
    _, _, week, prev, month = await bounds(pool, client_id, now)
    out: dict[str, dict] = collections.defaultdict(lambda: {'week': blank(), 'prev': blank(), 'month': blank(), 'example': {}})
    for r in await reviewed_rows(pool, client_id, month):
        acc = out[r['extension']]
        add(acc['month'], r)
        if r['call_started_at'] >= week:
            add(acc['week'], r)
        elif r['call_started_at'] >= prev:
            add(acc['prev'], r)
        if r['category'] and not r['success']:
            acc['example'].setdefault(r['category'], r['id'])
    return dict(out)
