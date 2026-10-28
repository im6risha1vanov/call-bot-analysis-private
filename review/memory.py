"""Память по менеджерам.

Слой 1, цифры (без модели): по каждому менеджеру за 7 дней, предыдущие 7 дней
и 30 дней — сколько разговоров разобрано, сколько с результатом и каким,
сколько потеряно с шансом и на каком месте (key_moment.category).

Слой 2, карточка (модель, раз в день после MANAGER_MEMORY_HOUR, по умолчанию
19:00): слабые места менеджера с примерами звонков и советом, что получается,
фокус на ближайшие дни и что изменилось. Промт в memory_prompt.md. Код
проверяет ответ: ID звонков только из входа, количества подставляет сам из
цифр. Карточки хранятся по дням в manager_memory (миграция 021), старые — история.
Включается MANAGER_MEMORY=on.

Этим пользуются карточка сотрудника в CRM (crm/people.py) и лист «Память
менеджеров» в Google-таблице (review/sheets.py). Ольга (KITCHEN_EXTENSIONS)
идёт по своему скрипту и в память не входит.

Ручной запуск: python -m review.memory build — карточки всем сейчас.
"""
from __future__ import annotations

import collections
import json
import logging
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import engine
from .render import CATEGORY_LABELS, OUTCOME_LABELS

log = logging.getLogger(__name__)
PROMPT_VERSION = 'manager_memory_v1'
PROMPT = (Path(__file__).with_name('memory_prompt.md').read_text(encoding='utf-8')
          .split('\n---\n', 1)[-1].strip())
MAX_TOKENS = int(os.getenv('MANAGER_MEMORY_MAX_TOKENS', '6000'))
MIN_TALKS = 5
MAX_MOMENTS = 40
FOCUS_RESULTS = {'получилось', 'частично', 'не получилось'}

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


# ---------------------------------------------------------------- карточка


def enabled() -> bool:
    return os.getenv('MANAGER_MEMORY', 'off').strip().lower() == 'on'


def hour() -> int:
    return int(os.getenv('MANAGER_MEMORY_HOUR', '19'))


def clip(value, size: int = 400) -> str | None:
    text = ' '.join(str(value or '').split())
    return text[:size] or None


def jsonable(stats: dict) -> dict:
    return {k: stats[k] for k in ('week', 'prev', 'month')}


async def latest(pool, client_id: int, ext: str):
    return await pool.fetchrow(
        'SELECT * FROM manager_memory WHERE client_id=$1 AND extension=$2 ORDER BY day DESC LIMIT 1', client_id, ext)


async def history(pool, client_id: int, ext: str, limit: int = 30) -> list:
    return await pool.fetch(
        'SELECT * FROM manager_memory WHERE client_id=$1 AND extension=$2 ORDER BY day DESC LIMIT $3', client_id, ext, limit)


async def collect(pool, client_id: int, ext: str, now: datetime | None = None) -> dict:
    """Всё, что модель видит о менеджере: цифры, потерянные моменты, удачные места, прошлая карточка."""
    tz, today, week, prev, month = await bounds(pool, client_id, now)
    rows = await reviewed_rows(pool, client_id, month, ext, with_data=True)
    stats = {'week': blank(), 'prev': blank(), 'month': blank()}
    moments, positives, wins = [], [], []
    for r in rows:
        add(stats['month'], r)
        if r['call_started_at'] >= week:
            add(stats['week'], r)
        elif r['call_started_at'] >= prev:
            add(stats['prev'], r)
        data = loads(r['data'])
        day = r['call_started_at'].astimezone(tz).strftime('%d.%m')
        if r['success']:
            if len(wins) < 12:
                wins.append({'id': r['id'], 'day': day, 'outcome': [OUTCOME_LABELS.get(o, o) for o in data.get('outcome') or []],
                             'rop_line': clip(data.get('rop_line'))})
        elif r['chance'] in ('hot', 'normal') and len(moments) < MAX_MOMENTS:
            moment = data.get('key_moment') if isinstance(data.get('key_moment'), dict) else {}
            moments.append({'id': r['id'], 'day': day, 'hot': r['chance'] == 'hot', 'category': moment.get('category'),
                            'what_happened': clip(moment.get('what_happened')), 'client_said': clip(moment.get('quote'), 200),
                            'say_instead': clip(moment.get('say_instead')),
                            'other_misses': [clip(m.get('what_happened')) for m in data.get('other_misses') or []
                                             if isinstance(m, dict) and m.get('what_happened')][:2]})
        for p in data.get('positives') or []:
            if isinstance(p, dict) and p.get('why') and len(positives) < 15:
                positives.append({'id': r['id'], 'day': day, 'why': clip(p['why'])})
    previous = await latest(pool, client_id, ext)
    return {'today': today, 'stats': stats, 'moments': moments, 'positives': positives, 'wins': wins,
            'previous': {'day': previous['day'].strftime('%d.%m'), **loads(previous['card'])} if previous else None,
            'ids': {r['id'] for r in rows}}


def build_input(name: str, info: dict) -> str:
    def period(acc):
        cats = {CATEGORY_LABELS.get(k, k) + f' ({k})': n for k, n in ranked(acc['categories'])}
        return {'разговоров': acc['talks'], 'с результатом': acc['success'], 'потеряно с шансом': acc['lost'],
                'горячих потерь': acc['hot'], 'причины потерь': cats}
    s = info['stats']
    payload = {
        'менеджер': name,
        'цифры': {'последние 7 дней': period(s['week']), 'предыдущие 7 дней': period(s['prev']), '30 дней': period(s['month'])},
        'потерянные разговоры (сначала новые)': info['moments'],
        'удачные места': info['positives'],
        'разговоры с результатом': info['wins'],
        'прошлая карточка': info['previous'],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def validate(card: dict, info: dict) -> dict:
    """Ответ модели в карточку: только ID звонков из входа, количества из цифр, короткие тексты."""
    card = card if isinstance(card, dict) else {}
    ids, s = info['ids'], info['stats']

    def examples(value, limit):
        out = []
        for x in value if isinstance(value, list) else []:
            try:
                x = int(x)
            except (TypeError, ValueError):
                continue
            if x in ids and x not in out:
                out.append(x)
        return out[:limit]

    weak = []
    for w in card.get('weak_spots') or []:
        if not isinstance(w, dict) or not clip(w.get('title')):
            continue
        category = w.get('category') if w.get('category') in CATEGORY_LABELS else 'other'
        counts = ({'month': s['month']['categories'].get(category, 0), 'week': s['week']['categories'].get(category, 0),
                   'prev': s['prev']['categories'].get(category, 0)} if category != 'other' else None)
        weak.append({'category': category, 'title': clip(w['title'], 120), 'pattern': clip(w.get('pattern')),
                     'advice': clip(w.get('advice')), 'examples': examples(w.get('examples'), 2), 'counts': counts})
    strengths = [{'title': clip(x['title'], 160), 'examples': examples(x.get('examples'), 1)}
                 for x in card.get('strengths') or [] if isinstance(x, dict) and clip(x.get('title'))]
    result = card.get('focus_result') if card.get('focus_result') in FOCUS_RESULTS else None
    return {'weak_spots': weak[:4], 'strengths': strengths[:3], 'focus': clip(card.get('focus'), 300),
            'trend': clip(card.get('trend'), 300), 'focus_result': result if info['previous'] else None,
            'previous_focus': (info['previous'] or {}).get('focus')}


async def update(pool, client_id: int, ext: str, name: str, now: datetime | None = None, call=None) -> dict:
    """Новая карточка менеджера на сегодня. call — запрос к модели (по умолчанию analysis._call)."""
    info = await collect(pool, client_id, ext, now)
    cost = 0.0
    if info['stats']['month']['talks'] < MIN_TALKS:
        card = validate({'trend': 'Мало разговоров, чтобы делать выводы'}, info)
    else:
        if call is None:
            from analysis import _call as call
        raw, cost = await call(PROMPT, build_input(name, info), MAX_TOKENS)
        card = validate(raw, info)
    await pool.execute(
        """INSERT INTO manager_memory(client_id, extension, day, card, stats, talks, prompt_version, cost_units)
           VALUES($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8)
           ON CONFLICT(client_id, extension, day) DO UPDATE SET card=EXCLUDED.card, stats=EXCLUDED.stats,
             talks=EXCLUDED.talks, prompt_version=EXCLUDED.prompt_version, cost_units=EXCLUDED.cost_units, created_at=now()""",
        client_id, ext, info['today'], json.dumps(card, ensure_ascii=False), json.dumps(jsonable(info['stats']), ensure_ascii=False),
        info['stats']['month']['talks'], PROMPT_VERSION, cost)
    if cost:
        from .prompts import add_spend
        await add_spend(pool, client_id, cost)
    return card


async def managers(pool, client_id: int) -> list[dict]:
    from . import kitchen
    rows = await pool.fetch(
        """SELECT extension, full_name FROM employees WHERE client_id=$1 AND role='manager' AND extension ~ '^[0-9]+$'
           ORDER BY extension::int""", client_id)
    return [{'ext': r['extension'], 'name': r['full_name'] or f"Доб. {r['extension']}"} for r in rows
            if not kitchen.handles(r['extension'])]


async def due(pool, client_id: int, ext: str, today) -> bool:
    """Карточки на сегодня нет и с прошлой появились новые разборы (или карточки ещё не было)."""
    last = await latest(pool, client_id, ext)
    if last is None:
        return True
    if last['day'] >= today:
        return False
    return bool(await pool.fetchval(
        """SELECT 1 FROM call_reviews r JOIN calls c ON c.id=r.call_id
           WHERE r.client_id=$1 AND c.extension=$2 AND r.created_at > $3 LIMIT 1""", client_id, ext, last['created_at']))


async def maybe_update(pool, now: datetime | None = None, call=None, force: bool = False) -> int:
    """Раз в день после MANAGER_MEMORY_HOUR: карточки тем, у кого есть новые разборы. force — всем сейчас."""
    if not (enabled() or force):
        return 0
    now = now or datetime.now(timezone.utc)
    done = 0
    for client in await pool.fetch('SELECT id, timezone FROM clients WHERE processing_enabled ORDER BY id'):
        local = now.astimezone(ZoneInfo(client['timezone'] or 'Europe/Moscow'))
        if not force and local.hour < hour():
            continue
        for m in await managers(pool, client['id']):
            try:
                if force or await due(pool, client['id'], m['ext'], local.date()):
                    await update(pool, client['id'], m['ext'], m['name'], now, call)
                    done += 1
            except Exception:
                log.exception('карточка памяти не обновилась, доб. %s', m['ext'])
    return done


if __name__ == '__main__':
    import asyncio
    import sys

    async def _main():
        import asyncpg
        from scripts.kitchen_backfill import load_env
        load_env(Path(__file__).resolve().parents[1] / '.env')
        pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=2)
        try:
            print('карточек обновлено:', await maybe_update(pool, force=True))
        finally:
            await pool.close()

    if sys.argv[1:] == ['build']:
        asyncio.run(_main())
