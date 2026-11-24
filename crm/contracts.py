"""Договоры: генератор .docx в CRM и журнал договоров.

Договор собирается в браузере (static/contracts/: docxtemplater по шаблонам из
crm/contract_templates/). Сервер отдаёт настройки и шаблоны, выдаёт сквозной номер,
пишет договор в журнал и ищет реквизиты по ИНН и БИК в DaData (ключ DADATA_KEY в .env,
на страницу он не попадает). Договор можно оформить из карточки сделки: тогда он
виден в её истории. Этап сделки при этом не меняется: «Договор отправлен» менеджер
отмечает сам, когда правда отправил.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from datetime import date
from pathlib import Path

import httpx

from tools import Forbidden, is_privileged
from . import deals, store

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).resolve().parent / 'contract_templates'
TYPES = {'ops': 'ОПС', 'gck': 'ГЦК', 'kc': 'КЦ отдельно', 'ds': 'Допсоглашение ГЦК + КЦ'}
# Тарифы обзвона в допсоглашении: название, диалогов, цена (None — цены пока нет, в выборе не показываем).
DS_TARIFFS = [['Старт', 300, 25000], ['Стандарт', 800, 55000], ['Профи', 2500, 165000], ['Макси', 12000, None]]
OUR_EMAIL = 'ivan.vashchuk.01@mail.ru'  # e-mail ИП Плетцер в реквизитах договора
DADATA_URL = 'https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/'
_templates: dict[str, str] = {}


def dadata_key() -> str:
    return re.sub(r'[^0-9a-zA-Z]', '', os.getenv('DADATA_KEY', ''))


def templates() -> dict[str, str]:
    if not _templates:
        for k in TYPES:
            _templates[k] = base64.b64encode((TEMPLATES / f'{k}.docx').read_bytes()).decode()
    return _templates


def config() -> dict:
    return {'ourEmail': os.getenv('CONTRACT_OUR_EMAIL', OUR_EMAIL), 'innLookup': bool(dadata_key()),
            'dsTariffs': DS_TARIFFS, 'templates': templates()}


async def next_number(pool, client_id: int) -> int:
    return await pool.fetchval('SELECT coalesce(max(num), 0) + 1 FROM contracts WHERE client_id=$1', client_id)


def amount_of(value) -> float | None:
    text = str(value if value is not None else '').replace(' ', '').replace(' ', '').replace(',', '.')
    if not text:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        raise ValueError('Неверная сумма.')


async def register(pool, actor, data: dict, name: str | None) -> dict:
    """Присвоить номер (кроме допсоглашения) и записать договор в журнал."""
    kind = str(data.get('type') or '')
    if kind not in TYPES:
        raise ValueError('Неизвестный договор.')
    try:
        day = date.fromisoformat(str(data.get('date') or '')[:10]) if data.get('date') else None
    except ValueError:
        raise ValueError('Неверная дата.')
    phone = None
    if data.get('deal'):
        phone = deals.key(data.get('deal'))
        if not phone:
            raise ValueError('Неверный номер сделки.')
        await deals.one(pool, actor, phone)  # сделка есть и она своя
    clip = lambda v, n: (str(v).strip()[:n] or None) if v is not None else None
    async with pool.acquire() as conn, conn.transaction():
        # Два менеджера одновременно не получат один номер: номер выдаётся под блокировкой.
        await conn.execute('SELECT pg_advisory_xact_lock(2510, $1)', actor.client_id)
        num = None if kind == 'ds' else await conn.fetchval(
            'SELECT coalesce(max(num), 0) + 1 FROM contracts WHERE client_id=$1', actor.client_id)
        await conn.execute(
            """INSERT INTO contracts(client_id, num, type, contract_date, company, inn, amount, tariff, base_num, phone,
                                     extension, author_tg, author_name)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
            actor.client_id, num, kind, day, clip(data.get('client'), 300), clip(data.get('inn'), 20),
            amount_of(data.get('sum')), clip(data.get('tariff'), 100), clip(data.get('baseNum'), 50), phone,
            actor.extension if str(actor.extension or '').isdigit() else None, actor.telegram_user_id, name)
    return {'num': num if num is not None else ''}


def row(r, tz) -> dict:
    return {'id': r['id'], 'num': r['num'], 'type': r['type'], 'type_title': TYPES.get(r['type'], r['type']),
            'date': r['contract_date'].isoformat() if r['contract_date'] else None, 'company': r['company'], 'inn': r['inn'],
            'amount': float(r['amount']) if r['amount'] is not None else None, 'tariff': r['tariff'], 'base_num': r['base_num'],
            'phone': r['phone'], 'ext': r['extension'], 'by': r['author_name'], 'created_at': r['created_at'].astimezone(tz).strftime('%Y-%m-%dT%H:%M')}


async def listing(pool, actor, phone: str | None = None) -> list[dict]:
    """Журнал: директор видит все договоры, менеджер — только свои."""
    own = None if is_privileged(actor) else actor.telegram_user_id
    rows = await pool.fetch(
        """SELECT * FROM contracts WHERE client_id=$1 AND ($2::bigint IS NULL OR author_tg=$2)
             AND ($3::text IS NULL OR phone=$3) ORDER BY created_at DESC, id DESC LIMIT 300""",
        actor.client_id, own, phone)
    tz = await store.zone(pool, actor.client_id)
    return [row(r, tz) for r in rows]


async def for_deal(pool, client_id: int, phone: str) -> list[dict]:
    if not await deals.has_table(pool, 'contracts'):
        return []
    tz = await store.zone(pool, client_id)
    return [row(r, tz) for r in await pool.fetch(
        'SELECT * FROM contracts WHERE client_id=$1 AND phone=$2 ORDER BY created_at, id', client_id, phone)]


async def lookup(kind: str, query: str) -> dict:
    """Реквизиты по ИНН (party) или БИК (bank) из DaData. Ответ DaData отдаём как есть, разбирает страница."""
    digits = re.sub(r'\D', '', str(query or ''))
    if kind == 'party' and len(digits) not in (10, 12):
        raise ValueError('В ИНН должно быть 10 цифр (организация) или 12 (ИП).')
    if kind == 'bank' and len(digits) != 9:
        raise ValueError('БИК должен быть из 9 цифр.')
    key = dadata_key()
    if not key:
        raise Forbidden('Поиск по ИНН не подключён.')
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(DADATA_URL + kind, json={'query': digits, 'count': 1},
                                  headers={'Authorization': 'Token ' + key, 'Accept': 'application/json'})
    except httpx.HTTPError:
        log.warning('DaData не ответила (%s)', kind)
        return {'ok': False, 'error': 'DaData не ответила. Попробуйте ещё раз или заполните вручную.'}
    if r.status_code in (401, 403):
        log.warning('DaData не принимает ключ: %s', r.status_code)
        return {'ok': False, 'error': 'DaData не принимает ключ. Скажите директору.'}
    if r.status_code != 200:
        return {'ok': False, 'error': f'DaData ответила {r.status_code}.'}
    found = (r.json() or {}).get('suggestions') or []
    if not found:
        return {'ok': False, 'error': 'Компания с таким ИНН не найдена.' if kind == 'party' else 'Банк с таким БИК не найден.'}
    return {'ok': True, 'raw': found[0]}
