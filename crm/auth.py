"""Вход в CRM по одноразовой ссылке из бота.

Бот по /crm выдаёт ссылку с токеном на 15 минут. Страница меняет его на
сессию в cookie на 30 дней. В базе лежат только хэши токенов. Кто вошёл,
решает строка employees по telegram_user_id, как и в боте: если сотрудника
отвяжут, сессия перестанет работать.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from tools import Actor, resolve_actor

LOGIN_TTL = timedelta(minutes=15)
SESSION_TTL = timedelta(days=30)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_login(pool, telegram_user_id: int) -> str:
    token = secrets.token_urlsafe(24)
    await pool.execute(
        'INSERT INTO crm_login_tokens(token_hash,telegram_user_id,expires_at) VALUES($1,$2,now()+$3::interval)',
        digest(token), telegram_user_id, LOGIN_TTL)
    return token


async def redeem(pool, token: str) -> str | None:
    """Одноразовый токен из ссылки в токен сессии или None."""
    if not token or len(token) > 100:
        return None
    tg = await pool.fetchval(
        """UPDATE crm_login_tokens SET used_at=now()
           WHERE token_hash=$1 AND used_at IS NULL AND expires_at>now() RETURNING telegram_user_id""", digest(token))
    if tg is None or await resolve_actor(pool, tg) is None:
        return None
    session = secrets.token_urlsafe(32)
    await pool.execute(
        'INSERT INTO crm_sessions(token_hash,telegram_user_id,expires_at) VALUES($1,$2,now()+$3::interval)',
        digest(session), tg, SESSION_TTL)
    return session


async def actor_for(pool, session: str | None) -> Actor | None:
    if not session or len(session) > 100:
        return None
    tg = await pool.fetchval(
        """UPDATE crm_sessions SET last_seen_at=now() WHERE token_hash=$1 AND expires_at>now()
           RETURNING telegram_user_id""", digest(session))
    return await resolve_actor(pool, tg) if tg is not None else None


async def logout(pool, session: str | None) -> None:
    if session:
        await pool.execute('DELETE FROM crm_sessions WHERE token_hash=$1', digest(session))
