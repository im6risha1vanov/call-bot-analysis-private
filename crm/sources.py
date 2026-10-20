"""Источники: сохранённые подборки доноров (страница «Источники», static/sources.js).

Подбор идёт в браузере: справочник ниш, оценка донора и закладка «Собрать номера» для 2ГИС и
Яндекс Карт — в static/sources-*.js. Сервер только хранит то, что сохранили кнопкой «Сохранить
подборку»: подборки общие для отдела, удалить может автор или руководитель.
"""
from __future__ import annotations

import json
import re

from tools import Forbidden, is_privileged

MAX_DONORS = 300
TYPES = {'direct', 'aggr', 'trigger', 'adjacent'}
LEVELS = {'good', 'mid', 'bad'}
SEGS = {'', 'eco', 'prem'}
FROM = {'', '2gis', 'ymaps', 'site'}


def text(value, limit: int) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def donor(d) -> dict:
    if not isinstance(d, dict):
        raise ValueError('Неверный донор.')
    phone = re.sub(r'\D', '', str(d.get('phone') or ''))[:15]
    site = text(d.get('site'), 200)
    if not phone and not site:
        raise ValueError('У донора нет ни телефона, ни сайта.')
    checks = d.get('checks') if isinstance(d.get('checks'), list) else []
    signals = d.get('signals') if isinstance(d.get('signals'), list) else []
    cat = d.get('cat')
    return {
        'name': text(d.get('name'), 200), 'phone': phone, 'site': site,
        'cat': cat if isinstance(cat, int) and 0 <= cat < 20 else 0,
        'checks': [bool(x) for x in checks[:4]], 'proven': bool(d.get('proven')),
        'from': d.get('from') if d.get('from') in FROM else '',
        'category': text(d.get('category'), 200), 'type': d.get('type') if d.get('type') in TYPES else 'direct',
        'level': d.get('level') if d.get('level') in LEVELS else 'mid', 'label': text(d.get('label'), 40),
        'signals': [text(s, 30) for s in signals[:8]],
    }


async def author_name(pool, who) -> str:
    name = None
    if who.telegram_user_id:
        name = await pool.fetchval('SELECT full_name FROM employees WHERE client_id=$1 AND telegram_user_id=$2 LIMIT 1',
                                   who.client_id, who.telegram_user_id)
    if name is None and str(who.extension or '').isdigit():
        name = await pool.fetchval('SELECT full_name FROM employees WHERE client_id=$1 AND extension=$2 LIMIT 1',
                                   who.client_id, who.extension)
    return name or ('Директор' if is_privileged(who) else '')


def mine(row, who) -> bool:
    if is_privileged(who):
        return True
    return bool(row['author_ext']) and row['author_ext'] == who.extension


async def save(pool, who, data: dict) -> dict:
    niche, niche_name = text(data.get('niche'), 40), text(data.get('niche_name'), 200)
    if not niche or not niche_name:
        raise ValueError('Сначала выберите нишу.')
    donors = data.get('donors')
    if not isinstance(donors, list) or not donors:
        raise ValueError('В подборке нет доноров.')
    if len(donors) > MAX_DONORS:
        raise ValueError(f'В подборке больше {MAX_DONORS} доноров.')
    seg = data.get('seg') if data.get('seg') in SEGS else ''
    values = (niche, niche_name, text(data.get('query'), 200), text(data.get('city'), 100), text(data.get('client'), 200), seg,
              json.dumps([donor(d) for d in donors], ensure_ascii=False))
    sid = data.get('id')
    if sid is not None:
        if not isinstance(sid, int):
            raise ValueError('Неверная подборка.')
        row = await pool.fetchrow('SELECT author_ext FROM source_selections WHERE id=$1 AND client_id=$2 AND deleted_at IS NULL',
                                  sid, who.client_id)
        if row is not None and mine(row, who):
            await pool.execute(
                """UPDATE source_selections SET niche=$2, niche_name=$3, query=$4, city=$5, project_client=$6, seg=$7,
                   donors=$8::jsonb, updated_at=now() WHERE id=$1""", sid, *values)
            return {'id': sid}
        # Чужую или удалённую подборку не трогаем: сохраняем как новую.
    sid = await pool.fetchval(
        """INSERT INTO source_selections(client_id, niche, niche_name, query, city, project_client, seg, donors,
           author_tg, author_ext, author_name) VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11) RETURNING id""",
        who.client_id, *values, who.telegram_user_id or None,
        who.extension if str(who.extension or '').isdigit() else None, await author_name(pool, who))
    return {'id': sid}


async def listing(pool, who) -> list[dict]:
    rows = await pool.fetch(
        """SELECT id, niche_name, city, project_client, author_name, author_ext, updated_at,
                  jsonb_array_length(donors) AS donors,
                  (SELECT count(*) FROM jsonb_array_elements(donors) d WHERE d->>'level'='good') AS good
           FROM source_selections WHERE client_id=$1 AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 200""",
        who.client_id)
    return [{'id': r['id'], 'niche_name': r['niche_name'], 'city': r['city'], 'client': r['project_client'],
             'author': r['author_name'], 'updated_at': r['updated_at'].isoformat(), 'donors': r['donors'], 'good': r['good'],
             'can_delete': mine(r, who)} for r in rows]


async def one(pool, who, sid: int) -> dict:
    r = await pool.fetchrow('SELECT * FROM source_selections WHERE id=$1 AND client_id=$2 AND deleted_at IS NULL', sid, who.client_id)
    if r is None:
        raise ValueError('Такой подборки нет.')
    return {'id': r['id'], 'niche': r['niche'], 'niche_name': r['niche_name'], 'query': r['query'], 'city': r['city'],
            'client': r['project_client'], 'seg': r['seg'] or '', 'donors': json.loads(r['donors']),
            'author': r['author_name'], 'updated_at': r['updated_at'].isoformat()}


async def delete(pool, who, sid: int) -> None:
    row = await pool.fetchrow('SELECT author_ext FROM source_selections WHERE id=$1 AND client_id=$2 AND deleted_at IS NULL',
                              sid, who.client_id)
    if row is None:
        raise ValueError('Такой подборки нет.')
    if not mine(row, who):
        raise Forbidden('Удалить подборку может её автор или руководитель.')
    await pool.execute('UPDATE source_selections SET deleted_at=now() WHERE id=$1', sid)
