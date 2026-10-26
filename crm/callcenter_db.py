"""Колл-центр в базе: для компаний без Google Таблицы колл-центра (модуль «Колл-центр»).

Клиенты колл-центра и их номера лежат в cc_clients и cc_leads. Наружу отдаём то же, что лист таблицы
«Прозвон лидов» (строки с теми же колонками и «Сводку»), поэтому кабинет клиента, сводка директора и
выгрузка считаются одним кодом с таблицей (crm/cabinet.py). Операторы — директор и менеджеры компании.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date

from . import company
from .cabinet import CALLBACK, INTERESTED, NOT_RELEVANT
from .store import parse_leads

NO_ANSWER = 'НДЗ'
RESULTS = {'no_answer': NO_ANSWER, 'callback': CALLBACK, 'interested': INTERESTED, 'not_relevant': NOT_RELEVANT}
MAX_ATTEMPTS = 3
PRICES = {'first': 10.0, 'repeat': 5.0, 'interested': 150.0}  # как в «Сводке» нашей таблицы
HEAD = ['id', 'Проект', 'Тег проекта', 'Телефон', 'Создано', 'Напоминание', 'Заметка', 'Комментарий',
        'Звонок 1 (первичный)', 'Звонок 2 (повторный)', 'Звонок 3 (повторный)', 'Сумма', 'Дата базы',
        'Дата звонка 1', 'Дата звонка 2', 'Дата звонка 3']


def ru(d) -> str:
    if not d:
        return ''
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return d.strftime('%d.%m.%Y')


def attempts_of(row) -> list[dict]:
    raw = row['attempts']
    return json.loads(raw) if isinstance(raw, str) else list(raw or [])


def lead_sum(attempts: list[dict], prices: dict) -> float:
    """Первичный звонок, повторные после недозвона и «Заинтересованный» по ценам компании."""
    total = 0.0
    for i, a in enumerate(attempts):
        total += prices['interested'] if a['r'] == INTERESTED else prices['first'] if i == 0 else prices['repeat']
    return total


def is_open(attempts: list[dict]) -> bool:
    """Номер ещё в работе: не было трёх попыток и нет итога «Заинтересованный» или «Неактуально»."""
    return len(attempts) < MAX_ATTEMPTS and not any(a['r'] in (INTERESTED, NOT_RELEVANT) for a in attempts)


async def prices(pool, client_id: int) -> dict:
    raw = (await company.info(pool, client_id))['settings'].get('callcenter_prices') or {}
    out = dict(PRICES)
    for key in PRICES:
        try:
            out[key] = float(raw[key])
        except (KeyError, TypeError, ValueError):
            pass
    return out


async def save_prices(pool, client_id: int, data: dict) -> dict:
    out = {}
    for key in PRICES:
        try:
            value = float(str(data.get(key, '')).replace(',', '.'))
        except ValueError:
            raise ValueError('Цена — число рублей.')
        if not 0 <= value <= 100000:
            raise ValueError('Цена — от 0 до 100 000 ₽.')
        out[key] = value
    await pool.execute(
        """INSERT INTO company_settings(client_id,settings) VALUES($1,jsonb_build_object('callcenter_prices',$2::jsonb))
           ON CONFLICT(client_id) DO UPDATE SET settings=company_settings.settings || jsonb_build_object('callcenter_prices',$2::jsonb),
             updated_at=now()""", client_id, json.dumps(out))
    return out


def tariffs(p: dict) -> list[dict]:
    return [{'name': 'Первичный звонок', 'price': p['first']}, {'name': 'Повторный звонок (после НДЗ)', 'price': p['repeat']},
            {'name': 'Звонок с результатом «Заинтересованный»', 'price': p['interested']}]


class DbSource:
    """Колл-центр компании в базе с тем же видом, что таблица: summary() и rows(имя клиента)."""
    mode = 'db'

    def __init__(self, pool, client_id: int):
        self.pool, self.client_id = pool, client_id

    async def summary(self) -> dict:
        p = await prices(self.pool, self.client_id)
        clients = []
        for c in await self.pool.fetch('SELECT id, name FROM cc_clients WHERE client_id=$1 ORDER BY id', self.client_id):
            first = repeat = interested = 0
            total = 0.0
            for r in await self.pool.fetch('SELECT attempts FROM cc_leads WHERE cc_client_id=$1', c['id']):
                a = attempts_of(r)
                first += bool(a)
                repeat += max(len(a) - 1, 0)
                interested += any(x['r'] == INTERESTED for x in a)
                total += lead_sum(a, p)
            clients.append({'name': c['name'], 'first': first, 'repeat': repeat, 'interested': interested, 'sum': total})
        return {'tariffs': tariffs(p), 'clients': clients}

    async def rows(self, name: str) -> list[list[str]]:
        p = await prices(self.pool, self.client_id)
        rows = await self.pool.fetch(
            """SELECT l.* FROM cc_leads l JOIN cc_clients c ON c.id=l.cc_client_id
               WHERE c.client_id=$1 AND c.name=$2 ORDER BY l.id""", self.client_id, name)
        out = [HEAD]
        for r in rows:
            a = attempts_of(r)
            calls = [x['r'] for x in a] + [''] * (MAX_ATTEMPTS - len(a))
            days = [ru(x['d']) for x in a] + [''] * (MAX_ATTEMPTS - len(a))
            out.append([str(r['id']), '', r['tag'], r['phone'], r['created_at'].strftime('%d.%m.%Y %H:%M'), r['remind'],
                        r['note'], r['comment'], *calls, f'{lead_sum(a, p):g} ₽', ru(r['base_date']), *days])
        return out


async def cc_client(pool, client_id: int, name: str) -> int:
    cid = await pool.fetchval('SELECT id FROM cc_clients WHERE client_id=$1 AND name=$2', client_id, name)
    if cid is None:
        raise ValueError('Такого клиента колл-центра нет.')
    return cid


async def add_client(pool, client_id: int, name) -> dict:
    name = ' '.join(str(name or '').split())
    if not name or len(name) > 80 or "'" in name:
        raise ValueError('Название клиента: от 1 до 80 символов, без апострофа.')
    if await pool.fetchval('SELECT 1 FROM cc_clients WHERE client_id=$1 AND name=$2', client_id, name):
        raise ValueError('Такой клиент уже есть.')
    await pool.execute('INSERT INTO cc_clients(client_id,name) VALUES($1,$2)', client_id, name)
    return {'name': name}


async def add_leads(pool, client_id: int, name: str, text: str, tag: str | None) -> dict:
    """Номера клиенту колл-центра: «номер; имя; компания; заметка» по строке. Повторы пропускаем."""
    cid = await cc_client(pool, client_id, name)
    found, bad = parse_leads(text)
    tag = ' '.join(str(tag or '').split())[:80]
    added = 0
    unique: dict[str, dict] = {}
    for f in found:  # повтор номера в той же вставке: берём первую строку
        unique.setdefault(f['number'], f)
    async with pool.acquire() as conn, conn.transaction():
        for f in unique.values():
            note = '; '.join(x for x in (f['name'], f['company'], f['note']) if x)[:500]
            added += await conn.fetchval(
                """WITH ins AS (INSERT INTO cc_leads(client_id,cc_client_id,phone,tag,note,base_date)
                   VALUES($1,$2,$3,$4,$5,current_date) ON CONFLICT DO NOTHING RETURNING 1) SELECT count(*) FROM ins""",
                client_id, cid, f['number'], tag, note)
    return {'added': added, 'duplicates': len(found) - added, 'invalid': len(bad), 'invalid_samples': bad[:5]}


async def work(pool, client_id: int, name: str | None = None) -> dict:
    """Кому звонить: сначала перезвоны, потом новые номера, потом недозвоны прошлых дней."""
    rows = await pool.fetch(
        """SELECT l.*, c.name AS client FROM cc_leads l JOIN cc_clients c ON c.id=l.cc_client_id
           WHERE l.client_id=$1 AND ($2::text IS NULL OR c.name=$2) ORDER BY l.id""", client_id, name)
    today = date.today().isoformat()
    items = []
    for r in rows:
        a = attempts_of(r)
        if not is_open(a):
            continue
        last = a[-1] if a else None
        if last and last['r'] == NO_ANSWER and last['d'] >= today:
            continue  # недозвон сегодня: следующая попытка завтра
        rank = 0 if last and last['r'] == CALLBACK else 1 if not a else 2
        items.append({'id': r['id'], 'client': r['client'], 'phone': r['phone'], 'tag': r['tag'], 'note': r['note'],
                      'comment': r['comment'], 'remind': r['remind'], 'attempts': [x['r'] for x in a], 'rank': rank})
    items.sort(key=lambda x: (x['rank'], x['id']))
    clients = [c['name'] for c in await pool.fetch('SELECT name FROM cc_clients WHERE client_id=$1 ORDER BY id', client_id)]
    return {'items': items[:300], 'total': len(items), 'clients': clients, 'results': RESULTS}


async def attempt(pool, client_id: int, lead_id: int, result: str, comment=None, remind=None, by: str | None = None) -> dict:
    if result not in RESULTS:
        raise ValueError('Неизвестный итог звонка.')
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow('SELECT * FROM cc_leads WHERE id=$1 AND client_id=$2 FOR UPDATE', lead_id, client_id)
        if row is None:
            raise ValueError('Такого номера нет.')
        a = attempts_of(row)
        if not is_open(a):
            raise ValueError('По этому номеру звонки уже закончены.')
        label = RESULTS[result]
        if label == NO_ANSWER:
            n = sum(x['r'].startswith(NO_ANSWER) for x in a) + 1
            label = NO_ANSWER if n == 1 else f'{NO_ANSWER} {n}'
        a.append({'r': label, 'd': date.today().isoformat(), 'by': by or ''})
        comment = str(comment or '').strip()[:500]
        await conn.execute(
            """UPDATE cc_leads SET attempts=$2::jsonb, comment=CASE WHEN $3='' THEN comment ELSE $3 END,
                 remind=$4, updated_at=now() WHERE id=$1""",
            lead_id, json.dumps(a, ensure_ascii=False), comment, str(remind or '').strip()[:80] if result == 'callback' else '')
    return {'id': lead_id, 'attempts': [x['r'] for x in a], 'open': is_open(a)}


async def export_csv(pool, client_id: int, name: str) -> str:
    """Лист клиента в CSV с колонками таблицы «Прозвон лидов»: открывается в Google Таблицах и Excel."""
    await cc_client(pool, client_id, name)
    buf = io.StringIO()
    csv.writer(buf).writerows(await DbSource(pool, client_id).rows(name))
    return '﻿' + buf.getvalue()
