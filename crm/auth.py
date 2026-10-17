"""Входы в CRM: директор, менеджер, клиент.

Пока вход один: тестовая ссылка /t/<CRM_TEST_KEY> даёт выбрать роль, сессия
хранит роль в себе, менеджер и клиент только смотрят. Личные сессии старого
входа (telegram_user_id) по-прежнему проверяются по employees, так что
отвязанный сотрудник теряет доступ. В базе лежат только хэши токенов.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import timedelta

from tools import Actor, resolve_actor

TEST_TTL = timedelta(days=7)
ROLES = ('director', 'manager', 'client')
ROLE_OF = {'owner': 'director', 'head': 'director', 'manager': 'manager'}


@dataclass(frozen=True)
class Viewer:
    role: str  # director | manager | client
    actor: Actor | None  # у клиента нет: он не сотрудник отдела
    client_key: str | None = None  # лист клиента в «Прозвон лидов»
    test: bool = False


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def test_key_ok(key: str | None) -> bool:
    expected = os.getenv('CRM_TEST_KEY') or ''
    return len(expected) >= 16 and bool(key) and hmac.compare_digest(str(key), expected)


async def test_login(pool, role: str, extension: str | None = None, client_key: str | None = None) -> str:
    if role not in ROLES:
        raise ValueError('Неизвестный вход.')
    client_id = await pool.fetchval('SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1')
    if client_id is None:
        raise ValueError('В базе нет отдела продаж.')
    if role == 'manager':
        if not await pool.fetchval("SELECT 1 FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'",
                                   client_id, str(extension or '')):
            raise ValueError('Такого менеджера нет.')
    else:
        extension = None
    if role == 'client':
        if not client_key:
            raise ValueError('Выберите клиента.')
    else:
        client_key = None
    session = secrets.token_urlsafe(32)
    await pool.execute(
        """INSERT INTO crm_sessions(token_hash,telegram_user_id,expires_at,role,client_id,extension,client_key,is_test)
           VALUES($1,NULL,now()+$2::interval,$3,$4,$5,$6,true)""",
        digest(session), TEST_TTL, role, client_id, extension, client_key)
    return session


async def viewer_for(pool, session: str | None) -> Viewer | None:
    if not session or len(session) > 100:
        return None
    row = await pool.fetchrow(
        """UPDATE crm_sessions SET last_seen_at=now() WHERE token_hash=$1 AND expires_at>now()
           RETURNING telegram_user_id, role, client_id, extension, client_key, is_test""", digest(session))
    if row is None:
        return None
    if row['role']:
        actor = None
        if row['role'] == 'director':
            actor = Actor(0, row['client_id'], 'owner', None)
        elif row['role'] == 'manager':
            actor = Actor(0, row['client_id'], 'manager', row['extension'])
        return Viewer(row['role'], actor, row['client_key'], row['is_test'])
    actor = await resolve_actor(pool, row['telegram_user_id']) if row['telegram_user_id'] is not None else None
    if actor is None:
        return None
    return Viewer(ROLE_OF.get(actor.role, 'manager'), actor)


async def actor_for(pool, session: str | None) -> Actor | None:
    who = await viewer_for(pool, session)
    return who.actor if who else None


async def logout(pool, session: str | None) -> None:
    if session:
        await pool.execute('DELETE FROM crm_sessions WHERE token_hash=$1', digest(session))
