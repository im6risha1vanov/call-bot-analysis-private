"""Сотрудники в CRM: кто есть в отделе, кто подключён к боту, кому идут копии разборов.

То же, что делал scripts/invite.sh, только с сайта и без перезапуска бота:
- добавить менеджера (добавочный Mango и имя) или руководителя и получить
  одноразовую ссылку-приглашение в бота t.me/<бот>?start=inv_<code> (invites.py);
- отключить человека от бота: он перестаёт получать сообщения, а его место
  можно отдать новому человеку новой ссылкой;
- включить или выключить полные копии разборов (раньше REVIEW_COPY_TO в .env).

Владельца отключить нельзя, себя тоже: так не потерять доступ к боту по ошибке.
"""
from __future__ import annotations

import json
import re
import secrets

from tools import Forbidden
from review import store as review_store
from . import telegram

ROLE_TITLES = {'owner': 'Владелец', 'head': 'Руководитель', 'manager': 'Менеджер'}
ORDER = {'owner': 0, 'head': 1, 'manager': 2}
LATIN = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
                 ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p', 'r', 's', 't',
                  'u', 'f', 'h', 'ts', 'ch', 'sh', 'sch', '', 'y', '', 'e', 'yu', 'ya']))


def head_extension(name: str) -> str:
    """head:<имя латиницей>, как у scripts/invite.sh."""
    slug = ''.join(LATIN.get(ch, ch) for ch in name.strip().lower())
    slug = re.sub(r'[^a-z0-9_.-]', '', slug)
    if not slug:
        raise ValueError('Имя должно содержать буквы.')
    return 'head:' + slug[:30]


def clean_name(value) -> str:
    name = ' '.join(str(value or '').split())
    if not name:
        raise ValueError('Укажите имя.')
    if len(name) > 80:
        raise ValueError('Слишком длинное имя.')
    return name


def clean_username(value) -> str | None:
    name = str(value or '').strip().lstrip('@').lower()
    if not name:
        return None
    if not re.fullmatch(r'[a-z0-9_]{4,32}', name):
        raise ValueError('Ник Telegram: латиница, цифры и _, от 4 символов.')
    return name


def matches(person: dict, token: str) -> bool:
    """Тот же разбор строки получателя, что в review.store.copy_chat_ids."""
    if re.fullmatch(r'-?\d+', token):
        return person['tg'] is not None and person['tg'] == int(token)
    name = (person['name'] or '').lower()
    return token == person['ext'] or name == token.lower() or name == '@' + token.lstrip('@').lower()


async def _people(pool, client_id: int) -> list[dict]:
    rows = await pool.fetch(
        """SELECT extension, role, full_name, telegram_user_id, telegram_username FROM employees
           WHERE client_id=$1""", client_id)
    people = [{'ext': r['extension'], 'role': r['role'], 'name': r['full_name'] or '', 'tg': r['telegram_user_id'],
               'username': r['telegram_username']} for r in rows]
    people.sort(key=lambda p: (ORDER.get(p['role'], 3), int(p['ext']) if p['ext'].isdigit() else 10**9, p['ext']))
    return people


async def listing(pool, actor) -> dict:
    people = await _people(pool, actor.client_id)
    tokens, source = await review_store.copy_tokens(pool, actor.client_id)
    invites = {r['extension']: r['until'] for r in await pool.fetch(
        """SELECT extension, max(expires_at) AS until FROM employee_invites
           WHERE client_id=$1 AND used_at IS NULL AND expires_at > now() GROUP BY extension""", actor.client_id)}
    sales = await pool.fetchval("SELECT to_jsonb(c)->'sales_extensions' FROM clients c WHERE id=$1", actor.client_id)
    sales = json.loads(sales) if isinstance(sales, str) else sales
    items = []
    for p in people:
        items.append({
            'ext': p['ext'], 'role': p['role'], 'role_title': ROLE_TITLES.get(p['role'], p['role']),
            'name': p['name'] or (f"Доб. {p['ext']}" if p['ext'].isdigit() else p['ext']),
            'linked': p['tg'] is not None, 'username': p['username'],
            'invite_until': invites[p['ext']].strftime('%Y-%m-%dT%H:%M:%S%z') if p['ext'] in invites else None,
            'copies': any(matches(p, t) for t in tokens),
            'reviewed': (p['ext'] in [str(x) for x in sales]) if isinstance(sales, list) and p['role'] == 'manager' else None,
            'can_unlink': p['tg'] is not None and p['role'] != 'owner' and p['tg'] != actor.telegram_user_id,
            'can_copy': p['role'] in ('head', 'owner'),
        })
    unknown = [t for t in tokens if not any(matches(p, t) for p in people)]
    return {'items': items, 'copies_source': source, 'copies_other': len(unknown)}


async def _person(pool, client_id: int, ext) -> dict:
    ext = str(ext or '')
    person = next((p for p in await _people(pool, client_id) if p['ext'] == ext), None)
    if person is None:
        raise ValueError('Такого сотрудника нет.')
    return person


async def invite(pool, actor, ext) -> dict:
    """Новая одноразовая ссылка в бота на 7 дней; прежние неиспользованные остаются в силе до своего срока."""
    person = await _person(pool, actor.client_id, ext)
    if person['tg'] is not None:
        raise ValueError('Этот человек уже подключён к боту.')
    bot = await telegram.bot_username()
    if not bot:
        raise ValueError('Не удалось узнать имя бота. Попробуйте позже.')
    code = secrets.token_hex(10)
    await pool.execute('INSERT INTO employee_invites(code, client_id, extension) VALUES($1,$2,$3)',
                       code, actor.client_id, person['ext'])
    return {'link': f'https://t.me/{bot}?start=inv_{code}', 'ext': person['ext']}


async def add(pool, actor, role: str, ext=None, name=None, username=None) -> dict:
    name, username = clean_name(name), clean_username(username)
    if role == 'manager':
        ext = str(ext or '').strip()
        if not re.fullmatch(r'\d{1,6}', ext):
            raise ValueError('Добавочный Mango: только цифры.')
    elif role == 'head':
        ext = head_extension(name)
    else:
        raise ValueError('Можно добавить менеджера или руководителя.')
    row = await pool.fetchrow('SELECT role, telegram_user_id FROM employees WHERE client_id=$1 AND extension=$2',
                              actor.client_id, ext)
    if row is not None and row['role'] != role:
        raise ValueError(f'Добавочный {ext} уже занят: там {ROLE_TITLES.get(row["role"], row["role"]).lower()}.')
    if row is not None and row['telegram_user_id'] is not None:
        raise ValueError('Этот человек уже подключён к боту. Чтобы поставить на его место другого, сначала отключите его.')
    await pool.execute(
        """INSERT INTO employees(client_id, extension, role, full_name, telegram_username) VALUES($1,$2,$3,$4,$5)
           ON CONFLICT(client_id, extension) DO UPDATE SET full_name=EXCLUDED.full_name,
             telegram_username=coalesce(EXCLUDED.telegram_username, employees.telegram_username)""",
        actor.client_id, ext, role, name, username)
    return await invite(pool, actor, ext)


async def unlink(pool, actor, ext) -> None:
    """Отключить от бота: сообщения больше не уходят, неиспользованные приглашения на это место гаснут."""
    person = await _person(pool, actor.client_id, ext)
    if person['tg'] is None:
        raise ValueError('Этот человек и так не подключён к боту.')
    if person['role'] == 'owner':
        raise Forbidden('Владельца отключить нельзя.')
    if person['tg'] == actor.telegram_user_id:
        raise Forbidden('Себя отключить нельзя.')
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('UPDATE employees SET telegram_user_id=NULL, telegram_username=NULL WHERE client_id=$1 AND extension=$2',
                           actor.client_id, person['ext'])
        await conn.execute('UPDATE employee_invites SET expires_at=now() WHERE client_id=$1 AND extension=$2 AND used_at IS NULL',
                           actor.client_id, person['ext'])


async def set_copies(pool, actor, ext, on: bool) -> None:
    """Полные копии разборов: только руководителям и владельцу, там звонки всех менеджеров."""
    person = await _person(pool, actor.client_id, ext)
    if on and person['role'] not in ('head', 'owner'):
        raise ValueError('Копии всех разборов можно включить только руководителю.')
    tokens, _ = await review_store.copy_tokens(pool, actor.client_id)
    tokens = [t for t in tokens if not matches(person, t)]
    if on:
        tokens.append(person['ext'])
    await pool.execute(
        """INSERT INTO review_copy_lists(client_id, tokens, updated_by) VALUES($1,$2,$3)
           ON CONFLICT(client_id) DO UPDATE SET tokens=EXCLUDED.tokens, updated_by=EXCLUDED.updated_by, updated_at=now()""",
        actor.client_id, tokens, actor.telegram_user_id or None)
