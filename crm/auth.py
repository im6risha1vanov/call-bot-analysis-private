"""Входы в CRM: директор, менеджер, клиент.

Личная ссылка /p/<token> или логин с паролем (их выдаёт директор в разделе
«Доступы») открывают CRM с ролью доступа на 90 дней; отзыв ссылки закрывает её сессии, а
менеджер, которого убрали из отдела, теряет доступ сразу. Тестовая ссылка
/t/<CRM_TEST_KEY> даёт выбрать любую роль; менеджер и клиент там только
смотрят. Сессия хранит роль в себе. В базе лежат только хэши токенов.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta

import crypto_util
from tools import Actor, home_client_id, resolve_actor

TEST_TTL = timedelta(days=7)
PERSONAL_TTL = timedelta(days=90)
ROLES = ('director', 'manager', 'client')
ROLE_OF = {'owner': 'director', 'head': 'director', 'manager': 'manager'}
LOGIN_RE = re.compile(r'[a-z0-9][a-z0-9._-]{2,39}')
PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'  # без l, i, o, 1, 0: не спутать при наборе
MAX_FAILS, LOCK_SECONDS = 5, 600  # после 5 неверных паролей логин отдыхает 10 минут
_fails: dict[str, tuple[int, float]] = {}


@dataclass(frozen=True)
class Viewer:
    role: str  # director | manager | client
    actor: Actor | None  # у клиента нет: он не сотрудник отдела
    client_key: str | None = None  # лист клиента в «Прозвон лидов»
    test: bool = False
    client_id: int | None = None  # компания (clients.id); у клиента компании actor нет, поэтому храним отдельно
    platform: bool = False  # наша учётка с отметкой platform: вкладка «Компании» (crm/platform.py)
    support: bool = False  # вход из вкладки «Компании»: «Посмотреть» (только просмотр) или «Сменить учётку»
    support_by: int | None = None  # чья это сессия на самом деле: наша учётка с отметкой platform
    invite_id: int | None = None  # учётная запись, под которой вошли

    @property
    def company(self) -> int | None:
        return self.actor.client_id if self.actor is not None else self.client_id


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


PUBLIC_KEY = 'open'  # /t/open — выбор входа для всех, когда сайт открыт (CRM_PUBLIC=1 в .env)


def public_on() -> bool:
    return os.getenv('CRM_PUBLIC') == '1'


def test_key_ok(key: str | None) -> bool:
    """Тестовая ссылка или открытый сайт. Входы по ним только для просмотра (is_test)."""
    if key == PUBLIC_KEY:
        return public_on()
    expected = os.getenv('CRM_TEST_KEY') or ''
    return len(expected) >= 16 and bool(key) and hmac.compare_digest(str(key), expected)


async def test_login(pool, role: str, extension: str | None = None, client_key: str | None = None) -> str:
    client_id = await home_client_id(pool)
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


def new_password() -> str:
    """Пароль вида kq7m-x3pa-9tdw: 12 случайных знаков, легко набрать на телефоне."""
    return '-'.join(''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(4)) for _ in range(3))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return 'scrypt$' + salt.hex() + '$' + hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1).hex()


def password_ok(password: str, stored: str | None) -> bool:
    try:
        kind, salt, want = (stored or '').split('$')
    except ValueError:
        return False
    got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()
    return kind == 'scrypt' and hmac.compare_digest(got, want)


def clean_login(login) -> str:
    login = str(login or '').strip().lower()
    if not LOGIN_RE.fullmatch(login):
        raise ValueError('Логин: от 3 до 40 латинских букв и цифр, можно точку, дефис и подчёркивание.')
    return login


async def login_free(pool, login: str) -> bool:
    return not await pool.fetchval('SELECT 1 FROM crm_invites WHERE lower(login)=$1 AND revoked_at IS NULL', login)


def clean_password(password) -> str:
    password = str(password or '').strip()
    if not 8 <= len(password) <= 64 or any(ch.isspace() for ch in password):
        raise ValueError('Пароль: от 8 до 64 знаков, без пробелов.')
    return password


async def account_create(pool, client_id: int, role: str, login, extension=None, client_key=None, name=None, tg=None,
                         password=None) -> str:
    """Учётная запись с логином. Пароль, если не задан, придумываем сами."""
    login = clean_login(login)
    if not await login_free(pool, login):
        raise ValueError('Такой логин уже есть.')
    password = clean_password(password) if password else new_password()
    await invite_create(pool, client_id, role, extension, client_key, name, tg, login=login,
                        password_hash=hash_password(password), password_enc=crypto_util.encrypt(password))
    return password


async def account_change(pool, client_id: int, invite_id: int, login=None, password=None, new=False) -> dict:
    """Директор меняет логин и/или пароль (new — придумать новый). Со сменой пароля открытые входы закрываются."""
    row = await pool.fetchrow('SELECT login FROM crm_invites WHERE id=$1 AND client_id=$2 AND login IS NOT NULL AND revoked_at IS NULL',
                              invite_id, client_id)
    if row is None:
        raise ValueError('Учётной записи нет или доступ отозван.')
    if login is not None and clean_login(login) != row['login']:
        login = clean_login(login)
        if not await login_free(pool, login):
            raise ValueError('Такой логин уже есть.')
        await pool.execute('UPDATE crm_invites SET login=$2 WHERE id=$1', invite_id, login)
    if new or password:
        password = new_password() if new else clean_password(password)
        await pool.execute('UPDATE crm_invites SET password_hash=$2, password_enc=$3 WHERE id=$1',
                           invite_id, hash_password(password), crypto_util.encrypt(password))
        await pool.execute('DELETE FROM crm_sessions WHERE invite_id=$1', invite_id)
    return next(i for i in await invites(pool, client_id) if i['id'] == invite_id)


def _shown_password(enc: str | None) -> str | None:
    try:
        return crypto_util.decrypt(enc) if enc else None
    except Exception:  # ключ шифрования сменили: пароль видно не будет, его можно задать заново
        return None


async def password_login(pool, login, password) -> tuple[str, str]:
    """Логин и пароль в (сессия, роль). Ошибку не уточняем: неверен логин или пароль."""
    login, password = str(login or '').strip().lower()[:40], str(password or '').strip()[:100]
    fails, until = _fails.get(login, (0, 0.0))
    if until > time.monotonic():
        raise ValueError('Слишком много неверных попыток. Подождите 10 минут.')
    row = await pool.fetchrow('SELECT id, password_hash FROM crm_invites WHERE lower(login)=$1 AND revoked_at IS NULL', login) \
        if login else None
    if row is None or not password_ok(password, row['password_hash']):
        fails += 1
        _fails[login] = (0, time.monotonic() + LOCK_SECONDS) if fails >= MAX_FAILS else (fails, 0.0)
        raise ValueError('Неверный логин или пароль.')
    _fails.pop(login, None)
    return await _open_session(pool, row['id'])


async def invite_create(pool, client_id: int, role: str, extension=None, client_key=None, name=None, tg=None,
                        login=None, password_hash=None, password_enc=None) -> str:
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
        """INSERT INTO crm_invites(token_hash,role,client_id,extension,client_key,name,telegram_user_id,login,password_hash,password_enc)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
        digest(token), role, client_id, extension, client_key, name, tg_id, login, password_hash, password_enc)
    return token


async def invite_login(pool, token: str | None) -> tuple[str, str] | None:
    """Ссылка из «Доступов» в (сессия, роль) или None. Ссылку можно открыть на нескольких устройствах."""
    if not token or len(token) > 100:
        return None
    invite_id = await pool.fetchval(
        'SELECT id FROM crm_invites WHERE token_hash=$1 AND revoked_at IS NULL AND login IS NULL', digest(token))
    return None if invite_id is None else await _open_session(pool, invite_id)


async def _open_session(pool, invite_id: int) -> tuple[str, str]:
    row = await pool.fetchrow(
        """UPDATE crm_invites SET last_used_at=now() WHERE id=$1
           RETURNING id, role, client_id, extension, client_key, telegram_user_id""", invite_id)
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
    return [{'id': r['id'], 'role': r['role'], 'ext': r['extension'], 'client': r['client_key'], 'login': r['login'],
             'password': _shown_password(r['password_enc']),
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
           RETURNING telegram_user_id, role, client_id, extension, client_key, is_test, invite_id, support_by""", digest(session))
    if row is None:
        return None
    extra = {'support': row['support_by'] is not None, 'support_by': row['support_by'], 'invite_id': row['invite_id']}
    if row['invite_id'] is not None:
        extra['platform'] = bool(await pool.fetchval('SELECT platform FROM crm_invites WHERE id=$1', row['invite_id']))
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
        return Viewer(row['role'], actor, row['client_key'], row['is_test'], row['client_id'], **extra)
    actor = await resolve_actor(pool, row['telegram_user_id']) if row['telegram_user_id'] is not None else None
    if actor is None:
        return None
    return Viewer(ROLE_OF.get(actor.role, 'manager'), actor, client_id=actor.client_id, **extra)


async def actor_for(pool, session: str | None) -> Actor | None:
    who = await viewer_for(pool, session)
    return who.actor if who else None


async def logout(pool, session: str | None) -> None:
    if session:
        await pool.execute('DELETE FROM crm_sessions WHERE token_hash=$1', digest(session))
