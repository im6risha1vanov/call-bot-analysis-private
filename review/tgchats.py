"""Чтение рабочего Telegram: переписки менеджеров с клиентами.

Бот входит в общий рабочий аккаунт как ещё одно устройство (Telethon, сессия в файле
TG_SESSION) и **только читает**: ничего не отправляет, не отмечает прочитанным, не
меняет папки. Менеджеры сами раскладывают клиентов по папкам Telegram Premium.

sync раз в TG_SYNC_MINUTES:
1. берёт только явно добавленные личные чаты из настроенных папок менеджеров;
2. дочитывает новые сообщения каждого чата: в первый раз за TG_HISTORY_DAYS дней.

Включается TG_CHATS=on. Нужны TG_API_ID и TG_API_HASH (приложение на my.telegram.org для
рабочего номера) и один вход на сервере: python -m review.tgchats login.
TG_FOLDER_OWNERS задаёт пары ID папки и добавочного, например 2:10,3:11,4:12.
ID можно посмотреть командой python -m review.tgchats folders. Переименование папки не меняет владельца.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import home_client_id

log = logging.getLogger(__name__)

HISTORY_DAYS = int(os.getenv('TG_HISTORY_DAYS', '30'))
SYNC_MINUTES = int(os.getenv('TG_SYNC_MINUTES', '30'))
MAX_DIALOGS = 1000
_last_sync: datetime | None = None


def enabled() -> bool:
    return (os.getenv('TG_CHATS', 'off').strip().lower() == 'on'
            and bool(os.getenv('TG_API_ID') and os.getenv('TG_API_HASH')))


def folder_owners() -> dict[int, str]:
    owners = {}
    raw = os.getenv('TG_FOLDER_OWNERS', '').strip()
    if not raw:
        return owners
    for item in raw.split(','):
        match = re.fullmatch(r'([1-9][0-9]*):([0-9]{1,6})', item.strip())
        if not match:
            raise ValueError('TG_FOLDER_OWNERS: укажите пары ID папки и добавочного, например 2:10,3:11.')
        folder_id, extension = int(match[1]), match[2]
        if folder_id in owners:
            raise ValueError('TG_FOLDER_OWNERS: ID папки указан дважды.')
        owners[folder_id] = extension
    return owners


def session_path() -> str:
    return os.getenv('TG_SESSION') or str(Path(__file__).resolve().parents[1] / 'tg-work.session')


def save_keys(env_path: Path, api_id: str, api_hash: str, backup_dir: Path = Path('/root/backups')) -> None:
    """Ключи приложения my.telegram.org в .env (прежние TG_API_* заменяются), до этого копия .env в backup_dir."""
    if not api_id.isdigit():
        raise ValueError('api_id — это число из my.telegram.org')
    if not re.fullmatch(r'[0-9a-f]{32}', api_hash):
        raise ValueError('api_hash — 32 символа 0-9 и a-f из my.telegram.org')
    text = env_path.read_text(encoding='utf-8') if env_path.exists() else ''
    if env_path.exists():
        backup = backup_dir / 'env.before-tg' if backup_dir.is_dir() else env_path.with_name('.env.before-tg')
        shutil.copy2(env_path, backup)
    lines = [line for line in text.splitlines() if line.split('=', 1)[0].strip() not in ('TG_API_ID', 'TG_API_HASH')]
    # Запись в тот же файл: владелец и права .env не меняются.
    env_path.write_text('\n'.join(lines + [f'TG_API_ID={api_id}', f'TG_API_HASH={api_hash}']) + '\n', encoding='utf-8')
    os.environ.update(TG_API_ID=api_id, TG_API_HASH=api_hash)


def make_client():
    from telethon import TelegramClient
    # receive_updates=False: бот не держит поток обновлений и не выглядит активным клиентом.
    return TelegramClient(session_path(), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH'],
                          receive_updates=False, device_model='Averon CRM (только чтение)')


def title_text(value) -> str:
    """Название папки: в новых версиях API это TextWithEntities, в старых строка."""
    return str(getattr(value, 'text', value) or '').strip()


def chat_kind(entity) -> str:
    name = type(entity).__name__
    if name == 'User':
        return 'user'
    if name == 'Channel' and not getattr(entity, 'megagroup', False):
        return 'channel'
    return 'group'


def in_folder(folder, peer_id: int, entity, explicit: dict) -> bool:
    """Попадает ли диалог в папку: явно добавлен или подходит по типу и не исключён."""
    if peer_id in explicit.get('include', set()):
        return True
    if peer_id in explicit.get('exclude', set()):
        return False
    kind = chat_kind(entity)
    if kind == 'user':
        if getattr(entity, 'bot', False):
            return bool(getattr(folder, 'bots', False))
        contact = bool(getattr(entity, 'contact', False))
        return bool(getattr(folder, 'contacts', False) and contact or getattr(folder, 'non_contacts', False) and not contact)
    if kind == 'group':
        return bool(getattr(folder, 'groups', False))
    return bool(getattr(folder, 'broadcasts', False))


async def folders(client) -> list[dict]:
    """Папки аккаунта: название и id чатов, добавленных явно / исключённых."""
    from telethon import utils
    from telethon.tl.functions.messages import GetDialogFiltersRequest
    result = await client(GetDialogFiltersRequest())
    out = []
    for f in getattr(result, 'filters', result):
        title = title_text(getattr(f, 'title', None))
        if not title:
            continue  # «Все чаты»
        ids = {'include': set(), 'exclude': set()}
        for key, attr in (('include', 'include_peers'), ('include', 'pinned_peers'), ('exclude', 'exclude_peers')):
            for peer in getattr(f, attr, None) or []:
                try:
                    ids[key].add(utils.get_peer_id(peer))
                except Exception:
                    continue
        out.append({'id': getattr(f, 'id', None), 'title': title, 'filter': f, 'ids': ids})
    return out


def media_kind(message) -> str | None:
    if getattr(message, 'voice', None):
        return 'voice'
    if getattr(message, 'photo', None):
        return 'photo'
    if getattr(message, 'document', None):
        return 'document'
    return type(message.media).__name__ if getattr(message, 'media', None) else None


async def sync(pool, client, client_id: int, now: datetime | None = None) -> dict:
    """Папки, чаты и новые сообщения. Возвращает счётчики для журнала."""
    now = now or datetime.now(timezone.utc)
    owners = folder_owners()
    employees = {r['extension'] for r in await pool.fetch(
        "SELECT extension FROM employees WHERE client_id=$1 AND role='manager' AND archived_at IS NULL", client_id)}
    found = await folders(client)
    configured = [f for f in found if owners.get(f['id']) in employees]
    memberships = {}
    for f in configured:
        for peer_id in f['ids']['include'] - f['ids']['exclude']:
            memberships.setdefault(peer_id, []).append(f)
    stats = {'folders': len(found), 'configured_folders': len(configured),
             'missing_mappings': len(owners) - len(configured), 'ambiguous': 0, 'chats': 0, 'messages': 0}
    selected = []
    if memberships:
        async for dialog in client.iter_dialogs(limit=MAX_DIALOGS):
            entity, peer_id = dialog.entity, dialog.id
            inside = memberships.get(peer_id)
            if not inside or chat_kind(entity) != 'user' or getattr(entity, 'bot', False) or getattr(entity, 'is_self', False):
                continue
            managers = {owners[f['id']] for f in inside}
            if len(managers) != 1:
                stats['ambiguous'] += 1
                continue
            selected.append((dialog, managers.pop(), [f['title'] for f in inside]))

    # Снять прежнее назначение до дочитывания истории: пропавший или спорный чат нельзя показывать прежнему менеджеру.
    stats['ownership_cleared'] = len(await pool.fetch(
        """UPDATE tg_chats c SET extension=NULL, folder_owner_verified=false, updated_at=now()
           WHERE c.client_id=$1 AND c.extension IS NOT NULL AND cardinality(c.folders)>0
             AND NOT EXISTS (SELECT 1 FROM unnest($2::bigint[], $3::text[]) AS owner(peer_id, extension)
                             WHERE owner.peer_id=c.peer_id AND owner.extension=c.extension)
           RETURNING c.peer_id""", client_id, [d.id for d, _, _ in selected], [e for _, e, _ in selected]))
    for dialog, extension, folder_titles in selected:
        entity, peer_id = dialog.entity, dialog.id
        row = await pool.fetchrow(
            """INSERT INTO tg_chats(client_id, peer_id, kind, title, username, phone, folders, extension, folder_owner_verified)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,true)
               ON CONFLICT(client_id, peer_id) DO UPDATE SET kind=EXCLUDED.kind, title=EXCLUDED.title,
                 username=EXCLUDED.username, phone=coalesce(tg_chats.phone, EXCLUDED.phone), folders=EXCLUDED.folders,
                 extension=EXCLUDED.extension, folder_owner_verified=true, updated_at=now()
               RETURNING last_message_id""",
            client_id, peer_id, 'user', dialog.name, getattr(entity, 'username', None),
            getattr(entity, 'phone', None), folder_titles, extension)
        stats['chats'] += 1
        last_id = row['last_message_id']
        # Чтение истории не отмечает сообщения прочитанными (это делает только mark_read).
        kwargs = {'min_id': last_id} if last_id else {'offset_date': now - timedelta(days=HISTORY_DAYS)}
        newest = None
        async for m in client.iter_messages(entity, reverse=True, **kwargs):
            if not getattr(m, 'date', None):
                continue
            await pool.execute(
                """INSERT INTO tg_messages(client_id, peer_id, message_id, sent_at, outgoing, text, media)
                   VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING""",
                client_id, peer_id, m.id, m.date, bool(m.out), m.message or None, media_kind(m))
            stats['messages'] += 1
            newest = m
        if newest is not None:
            await pool.execute('UPDATE tg_chats SET last_message_id=$3, last_message_at=$4 WHERE client_id=$1 AND peer_id=$2',
                               client_id, peer_id, newest.id, newest.date)
    return stats


async def maybe_run(pool, now: datetime | None = None) -> dict | None:
    global _last_sync
    if not enabled():
        return None
    now = now or datetime.now(timezone.utc)
    if _last_sync and now - _last_sync < timedelta(minutes=SYNC_MINUTES):
        return None
    _last_sync = now
    client_id = await home_client_id(pool)  # рабочий Telegram из .env — наш
    if client_id is None:
        return None
    client = make_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            log.error('Telegram: сессия не авторизована, нужен вход: python -m review.tgchats login')
            return None
        stats = await sync(pool, client, client_id, now)
        log.info('Telegram: папок %s, чатов %s, новых сообщений %s, папок менеджеров %s, '
                 'не найдены настройки для %s назначений, спорных чатов %s, снято прежних назначений %s',
                 stats['folders'], stats['chats'], stats['messages'], stats['configured_folders'],
                 stats['missing_mappings'], stats['ambiguous'], stats['ownership_cleared'])
        return stats
    finally:
        await client.disconnect()


if __name__ == '__main__':
    # python -m review.tgchats login — один раз: вход в рабочий аккаунт (номер, код из Telegram, пароль).
    # python -m review.tgchats folders — только чтение: какие папки видит бот и сколько в них чатов.
    # python -m review.tgchats status — настройка синхронизации, без обращения к Telegram.
    # python -m review.tgchats setup — спросить api_id и api_hash (или получить их с my.telegram.org, review/tgapp.py),
    # записать в .env и сразу войти (login).
    import asyncio
    import sys

    async def _main(command):
        from scripts.kitchen_backfill import load_env
        env = Path(__file__).resolve().parents[1] / '.env'
        load_env(env)
        if command == 'status':
            print('Синхронизация:', 'включена' if enabled() else 'выключена или отсутствуют ключи')
            print('Файл сессии:', 'есть' if Path(session_path()).is_file() else 'отсутствует')
            print('Настроено папок:', len(folder_owners()))
            return
        if command == 'setup':
            if os.getenv('TG_API_ID') and os.getenv('TG_API_HASH'):
                print('Ключи уже есть в .env, сразу вход.')
            else:
                api_id = input('api_id (пусто: получить ключи на my.telegram.org прямо с сервера): ').strip()
                if api_id:
                    import getpass
                    api_hash = getpass.getpass('api_hash (при вводе не видно): ').strip()
                else:
                    from .tgapp import TgAppError, fetch_keys, site_phone
                    try:
                        phone = site_phone(input('Номер для my.telegram.org, любой твой, например личный: '))
                        api_id, api_hash = fetch_keys(
                            phone, lambda: input('Код из Telegram (чат «Telegram» на этом номере): ').strip())
                    except TgAppError as exc:
                        print('Не вышло:', exc)
                        return
                    print('Ключи получены, api_id', api_id)
                save_keys(env, api_id, api_hash)
                print('Ключи записаны в .env. Теперь вход в рабочий аккаунт: номер рабочего телефона и код.')
            command = 'login'
        client = make_client()
        if command == 'login':
            await client.start()
            me = await client.get_me()
            print('Вход выполнен:', me.first_name, me.phone, '· сессия', session_path())
            os.chmod(session_path(), 0o600)
            if os.geteuid() == 0:
                # Запуск от root: сессию отдаём владельцу папки приложения (пользователь сервиса).
                owner = env.parent.stat()
                os.chown(session_path(), owner.st_uid, owner.st_gid)
        else:
            await client.connect()
            if not await client.is_user_authorized():
                print('Сессии нет: сначала python -m review.tgchats login')
                return
            found = await folders(client)
            counts = {f['id']: 0 for f in found}
            async for dialog in client.iter_dialogs(limit=MAX_DIALOGS):
                for f in found:
                    if in_folder(f['filter'], dialog.id, dialog.entity, f['ids']):
                        counts[f['id']] += 1
            for f in found:
                print(f'папка {f["id"]} «{f["title"]}»: чатов {counts[f["id"]]}')
            if not found:
                print('папок нет')
        await client.disconnect()

    if sys.argv[1:2] in (['login'], ['folders'], ['setup'], ['status']):
        asyncio.run(_main(sys.argv[1]))
