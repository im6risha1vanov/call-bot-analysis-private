"""Входы в CRM: директор, менеджер, клиент.

Личная ссылка /p/<token> (её выдаёт директор в разделе «Доступы») открывает
CRM с ролью из ссылки на 90 дней; отзыв ссылки закрывает её сессии, а
менеджер, которого убрали из отдела, теряет доступ сразу. Тестовая ссылка
/t/<CRM_TEST_KEY> даёт выбрать любую роль; менеджер и клиент там только
смотрят. Сессия хранит роль в себе. В базе лежат только хэши токенов.
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
PERSONAL_TTL = timedelta(days=90)
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
    client_id = await pool.fetchval('SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1')
    if client_id is None:
        raise ValueError('В базе нет отдела продаж.')
    extension, client_key = await check_target(pool, client_id, role, extension, client_key)
    session = secrets.token_urlsafe(32)
    await pool.execute(
        """INSERT INTO crm_sessions(token_hash,telegram_user_id,expires_at,role,client_id,extension,client_key,is_test)
           VALUES($1,NULL,now()+$2::interval,$3,$4,$5,$6,true)""",
        digest(session), TEST_TTL, role, client_id, extension, client_key)
    return session


async def check_target(pool, client_id: int, role: str, extension, client_key) -> tuple[str | None, str | None]:
    if role not in ROLES:
        raise ValueError('Неизвестный вход.')
    if role == 'manager':
        if not await pool.fetchval("SELECT 1 FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'",
                                   client_id, str(extension or '')):
            raise ValueError('Такого менеджера нет.')
        return str(extension), None
    if role == 'client':
        if not client_key:
            raise ValueError('Выберите клиента.')
        return None, client_key
    return None, None


async def directors(pool, client_id: int) -> list[dict]:
    """Руководители и владелец, подключённые к боту: к ним можно привязать ссылку директора."""
    rows = await pool.fetch(
        """SELECT telegram_user_id, full_name, role FROM employees WHERE client_id=$1 AND role IN ('owner','head')
           AND telegram_user_id IS NOT NULL ORDER BY role DESC, full_name""", client_id)
    return [{'tg': str(r['telegram_user_id']), 'name': r['full_name'] or str(r['telegram_user_id'])} for r in rows]


async def invite_create(pool, client_id: int, role: str, extension=None, client_key=None, name=None, tg=None) -> str:
    """Личная ссылка. Токен показываем один раз, в базе только хэш.

    tg — чья это ссылка директора: тогда в CRM он работает под своим Telegram ID
    (личные промты), а если его отключат от бота, ссылка перестанет работать."""
    extension, client_key = await check_target(pool, client_id, role, extension, client_key)
    name = (str(name or '').strip() or None)
    if name and len(name) > 80:
        raise ValueError('Слишком длинное имя.')
    tg_id = None
    if role == 'director' and tg not in (None, ''):
        person = next((d for d in await directors(pool, client_id) if d['tg'] == str(tg)), None)
        if person is None:
            raise ValueError('Этого руководителя нет в боте.')
        tg_id, name = int(person['tg']), name or person['name']
    token = secrets.token_urlsafe(24)
    await pool.execute(
        'INSERT INTO crm_invites(token_hash,role,client_id,extension,client_key,name,telegram_user_id) VALUES($1,$2,$3,$4,$5,$6,$7)',
        digest(token), role, client_id, extension, client_key, name, tg_id)
    return token


async def invite_login(pool, token: str | None) -> tuple[str, str] | None:
    """Ссылка из «Доступов» в (сессия, роль) или None. Ссылку можно открыть на нескольких устройствах."""
    if not token or len(token) > 100:
        return None
    row = await pool.fetchrow(
        """UPDATE crm_invites SET last_used_at=now() WHERE token_hash=$1 AND revoked_at IS NULL
           RETURNING id, role, client_id, extension, client_key, telegram_user_id""", digest(token))
    if row is None:
        return None
    session = secrets.token_urlsafe(32)
    await pool.execute(
        """INSERT INTO crm_sessions(token_hash,telegram_user_id,expires_at,role,client_id,extension,client_key,is_test,invite_id)
           VALUES($1,$8,now()+$2::interval,$3,$4,$5,$6,false,$7)""",
        digest(session), PERSONAL_TTL, row['role'], row['client_id'], row['extension'], row['client_key'], row['id'],
        row['telegram_user_id'])
    return session, row['role']


async def invites(pool, client_id: int) -> list[dict]:
    rows = await pool.fetch(
        """SELECT i.*, e.full_name FROM crm_invites i
           LEFT JOIN employees e ON e.client_id=i.client_id AND e.extension=i.extension AND e.role='manager'
           WHERE i.client_id=$1 AND i.revoked_at IS NULL ORDER BY i.role, i.created_at""", client_id)
    fmt = lambda v: v.strftime('%Y-%m-%dT%H:%M:%S%z') if v else None  # noqa: E731
    return [{'id': r['id'], 'role': r['role'], 'ext': r['extension'], 'client': r['client_key'],
             'name': r['name'] or r['full_name'] or r['client_key'] or '', 'tg': bool(r['telegram_user_id']),
             'created': fmt(r['created_at']),
             'used': fmt(r['last_used_at'])} for r in rows]


async def invite_revoke(pool, client_id: int, invite_id: int) -> None:
    done = await pool.fetchval(
        'UPDATE crm_invites SET revoked_at=now() WHERE id=$1 AND client_id=$2 AND revoked_at IS NULL RETURNING id',
        invite_id, client_id)
    if done is None:
        raise ValueError('Ссылка уже отозвана.')
    await pool.execute('DELETE FROM crm_sessions WHERE invite_id=$1', invite_id)


async def viewer_for(pool, session: str | None) -> Viewer | None:
    if not session or len(session) > 100:
        return None
    row = await pool.fetchrow(
        """UPDATE crm_sessions s SET last_seen_at=now() WHERE token_hash=$1 AND expires_at>now()
             AND (invite_id IS NULL OR EXISTS(SELECT 1 FROM crm_invites i WHERE i.id=s.invite_id AND i.revoked_at IS NULL))
           RETURNING telegram_user_id, role, client_id, extension, client_key, is_test""", digest(session))
    if row is None:
        return None
    if row['role']:
        actor = None
        if row['role'] == 'director' and row['telegram_user_id']:
            # Ссылка конкретного руководителя: отключили его от бота — ссылка не работает.
            actor = await resolve_actor(pool, row['telegram_user_id'])
            if actor is None or actor.client_id != row['client_id'] or actor.role not in ('owner', 'head'):
                return None
        elif row['role'] == 'director':
            actor = Actor(0, row['client_id'], 'owner', None)
        elif row['role'] == 'manager':
            # Менеджера убрали из отдела: личная ссылка перестаёт работать сразу.
            tg = await pool.fetchrow("SELECT telegram_user_id FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'",
                                     row['client_id'], row['extension'])
            if tg is None:
                return None
            actor = Actor(tg['telegram_user_id'] or 0, row['client_id'], 'manager', row['extension'])
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
