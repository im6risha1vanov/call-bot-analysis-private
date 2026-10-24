"""Блоки-действия конструктора автоматизаций: Telegram, почта, Google-таблица, вебхук.

Каждый блок — заранее написанный и проверенный код. Агент и редактор только
выбирают блок и заполняют его поля; код, написанный моделью, не запускается никогда.
У блока есть описание полей (по нему проверяется сценарий и строится форма) и run().

Ошибки: Retry — временная (сеть, 5xx, 429), движок повторит; Fail — повтор не поможет
(неверный адрес, нет доступа, аккаунт отключён), её надо чинить в настройках.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import smtplib
import socket
from email.message import EmailMessage
from urllib.parse import quote, urlsplit

import httpx

log = logging.getLogger(__name__)
TIMEOUT = 15
EMAIL = re.compile(r'[^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,}')
MAX_TEXT = 3500
MAX_COLUMNS = 26
MAX_EMAILS = 5


class Retry(Exception):
    """Временная ошибка: повторить позже."""


class Fail(Exception):
    """Повтор не поможет: нужно поправить сценарий или подключение."""


def proxy_url() -> str | None:
    """Прокси для зарубежных сервисов (AUTOMATION_PROXY=http://user:pass@host:port или socks5://...)."""
    return os.getenv('AUTOMATION_PROXY', '').strip() or None


def client(use_proxy: bool) -> httpx.AsyncClient:
    if use_proxy and not proxy_url():
        raise Fail('Прокси не настроен на сервере (AUTOMATION_PROXY).')
    return httpx.AsyncClient(timeout=TIMEOUT, proxy=proxy_url() if use_proxy else None, follow_redirects=False)


def check_status(r: httpx.Response, what: str):
    if r.status_code == 429 or r.status_code >= 500:
        raise Retry(f'{what}: сервис ответил {r.status_code}, попробуем позже.')
    if r.status_code >= 400:
        raise Fail(f'{what}: сервис ответил {r.status_code}. {r.text[:200]}')


# --- Telegram -------------------------------------------------------------------------------------

async def telegram_send(params: dict, account: dict | None, text: str) -> str:
    token = os.getenv('BOT_TOKEN')
    if not token:
        raise Fail('У CRM нет бота (BOT_TOKEN).')
    if not account or not account.get('chat_id'):
        raise Fail('Чат Telegram не подключён.')
    try:
        async with client(bool(params.get('proxy'))) as http:
            r = await http.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                json={'chat_id': account['chat_id'], 'text': text[:MAX_TEXT],
                                      'disable_web_page_preview': True})
    except httpx.HTTPError as exc:
        raise Retry(f'Telegram недоступен: {type(exc).__name__}')
    if r.status_code in (400, 403):
        # бота удалили из группы, заблокировали или чат стал супергруппой
        raise Fail('Telegram не принял сообщение: бот не может писать в этот чат. Подключите чат заново.')
    check_status(r, 'Telegram')
    return text[:MAX_TEXT]


# --- Почта ----------------------------------------------------------------------------------------

def smtp_ready() -> bool:
    return bool(os.getenv('SMTP_HOST') and os.getenv('SMTP_FROM'))


def _smtp_send(msg: EmailMessage):
    host, port = os.environ['SMTP_HOST'], int(os.getenv('SMTP_PORT', '465'))
    user, password = os.getenv('SMTP_USER'), os.getenv('SMTP_PASSWORD')
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT)
    else:
        server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        server.starttls()
    with server:
        if user:
            server.login(user, password or '')
        server.send_message(msg)


async def email_send(params: dict, subject: str, text: str) -> str:
    if not smtp_ready():
        raise Fail('Почта для уведомлений не настроена на сервере (SMTP_HOST, SMTP_FROM).')
    msg = EmailMessage()
    msg['From'] = os.environ['SMTP_FROM']
    msg['To'] = ', '.join(params['to'])
    msg['Subject'] = subject[:200] or 'Уведомление CRM'
    msg.set_content(text)
    try:
        await asyncio.to_thread(_smtp_send, msg)
    except smtplib.SMTPRecipientsRefused:
        raise Fail('Почтовый сервер не принял адрес получателя.')
    except (smtplib.SMTPException, OSError) as exc:
        raise Retry(f'Почта не отправилась: {type(exc).__name__}')
    return f'{subject}\n{text}'


# --- Google-таблица -------------------------------------------------------------------------------

def service_email() -> str | None:
    """Адрес сервисного аккаунта: ему клиент даёт доступ редактора к своей таблице."""
    path = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE')
    try:
        with open(path or '', encoding='utf-8') as f:
            return json.load(f).get('client_email')
    except (OSError, ValueError):
        return None


async def google_token() -> str:
    from review.sheets import token
    return await asyncio.to_thread(token)


SHEET_URL = re.compile(r'/spreadsheets/d/([A-Za-z0-9_-]{20,})')


def sheet_id(text: str) -> str | None:
    text = (text or '').strip()
    m = SHEET_URL.search(text)
    if m:
        return m[1]
    return text if re.fullmatch(r'[A-Za-z0-9_-]{20,}', text) else None


async def sheets_probe(spreadsheet: str, use_proxy: bool = False) -> dict:
    """Название таблицы и листов. Нет доступа — Fail с подсказкой, кому его дать."""
    try:
        async with client(use_proxy) as http:
            r = await http.get(f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet}',
                               params={'fields': 'properties.title,sheets.properties.title'},
                               headers={'Authorization': 'Bearer ' + await google_token()})
    except httpx.HTTPError as exc:
        raise Retry(f'Google недоступен: {type(exc).__name__}')
    if r.status_code in (403, 404):
        raise Fail(f'Нет доступа к таблице. Откройте «Настройки доступа» и дайте права редактора адресу '
                   f'{service_email() or "сервисного аккаунта"}.')
    check_status(r, 'Google')
    data = r.json()
    return {'title': data['properties']['title'], 'sheets': [s['properties']['title'] for s in data.get('sheets', [])]}


async def sheets_append(params: dict, account: dict | None, row: list[str]) -> str:
    if not account or not account.get('spreadsheet_id'):
        raise Fail('Google-таблица не подключена.')
    sheet = account.get('sheet') or 'Лист1'
    rng = quote(f"'{sheet}'!A1", safe='')
    try:
        async with client(bool(params.get('proxy'))) as http:
            # RAW: текст из CRM не превращается в формулы таблицы
            r = await http.post(
                f'https://sheets.googleapis.com/v4/spreadsheets/{account["spreadsheet_id"]}/values/{rng}:append',
                params={'valueInputOption': 'RAW', 'insertDataOption': 'INSERT_ROWS'},
                headers={'Authorization': 'Bearer ' + await google_token()}, json={'values': [row]})
    except httpx.HTTPError as exc:
        raise Retry(f'Google недоступен: {type(exc).__name__}')
    if r.status_code in (403, 404):
        raise Fail(f'Нет доступа к таблице или листа «{sheet}» нет. Доступ редактора нужен адресу '
                   f'{service_email() or "сервисного аккаунта"}.')
    if r.status_code == 400:
        raise Fail(f'Google не принял строку: {r.text[:200]}')
    check_status(r, 'Google')
    return ' | '.join(row)


# --- Вебхук ---------------------------------------------------------------------------------------

def public_address(host: str) -> bool:
    """Адрес снаружи: в нашу сеть, на сам сервер и в служебные адреса вебхук не ходит."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split('%')[0])
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


def check_url(url: str) -> str:
    url = (url or '').strip()
    parts = urlsplit(url)
    if parts.scheme not in ('https', 'http') or not parts.hostname:
        raise ValueError('Адрес вебхука должен начинаться с https://')
    if parts.username or parts.password:
        raise ValueError('Логин и пароль в адресе вебхука не поддерживаются.')
    if len(url) > 1000:
        raise ValueError('Слишком длинный адрес вебхука.')
    return url


async def webhook_send(params: dict, event: str, payload: dict) -> str:
    url = check_url(params['url'])
    host = urlsplit(url).hostname
    use_proxy = bool(params.get('proxy'))
    if not use_proxy and not await asyncio.to_thread(public_address, host):
        raise Fail('Адрес вебхука ведёт во внутреннюю сеть или не существует.')
    body = {'event': event, 'data': payload}
    try:
        async with client(use_proxy) as http:
            r = await http.post(url, json=body, headers={'User-Agent': 'AveronCRM-Automations/1'})
    except httpx.HTTPError as exc:
        raise Retry(f'Адрес вебхука не ответил: {type(exc).__name__}')
    check_status(r, 'Вебхук')
    return f'POST {url} → {r.status_code}'


# --- Каталог: что видит агент и редактор ----------------------------------------------------------

ACTIONS = {
    'telegram.send': {
        'title': 'Сообщение в Telegram',
        'about': 'Пишет в подключённый чат или группу от бота CRM.',
        'account': 'telegram',
        'fields': {'text': 'текст с полями события в фигурных скобках, например «Оплата: {deal.amount} ₽»',
                   'proxy': 'true — отправлять через прокси (необязательно)'},
    },
    'email.send': {
        'title': 'Письмо',
        'about': 'Письмо с адреса CRM на 1–5 адресов.',
        'account': None,
        'fields': {'to': 'список адресов', 'subject': 'тема (можно с полями)', 'text': 'текст (можно с полями)'},
    },
    'sheets.append': {
        'title': 'Строка в Google-таблицу',
        'about': 'Добавляет строку в конец листа подключённой таблицы.',
        'account': 'sheets',
        'fields': {'row': 'список значений по столбцам, каждое можно с полями, например ["{date}", "{deal.amount}"]',
                   'proxy': 'true — через прокси (необязательно)'},
    },
    'webhook.send': {
        'title': 'Вебхук (POST JSON)',
        'about': 'Отправляет событие целиком на адрес другой системы: {"event": ..., "data": {...}}.',
        'account': None,
        'fields': {'url': 'адрес https://...', 'proxy': 'true — через прокси (необязательно)'},
    },
}


def check_action(action: dict, accounts: dict[int, dict]) -> dict:
    """Проверенная копия действия или ValueError с понятной причиной."""
    if not isinstance(action, dict):
        raise ValueError('Действие описано неверно.')
    kind = action.get('type')
    meta = ACTIONS.get(kind)
    if meta is None:
        raise ValueError(f'Такого действия нет: {kind}. Есть: {", ".join(ACTIONS)}.')
    out = {'type': kind}
    if meta['account']:
        try:
            acc_id = int(action.get('account'))
        except (TypeError, ValueError):
            raise ValueError(f'«{meta["title"]}»: не выбран подключённый аккаунт.')
        acc = accounts.get(acc_id)
        if acc is None or acc['kind'] != meta['account']:
            raise ValueError(f'«{meta["title"]}»: такого подключения нет.')
        out['account'] = acc_id
    if action.get('proxy'):
        out['proxy'] = True
    if kind == 'telegram.send':
        text = str(action.get('text') or '').strip()
        if not text:
            raise ValueError('«Сообщение в Telegram»: пустой текст.')
        out['text'] = text[:MAX_TEXT]
    elif kind == 'email.send':
        to = action.get('to')
        to = [to] if isinstance(to, str) else (to or [])
        to = [str(x).strip() for x in to if str(x).strip()]
        if not to or len(to) > MAX_EMAILS or not all(EMAIL.fullmatch(x) for x in to):
            raise ValueError(f'«Письмо»: нужно от 1 до {MAX_EMAILS} правильных адресов.')
        text = str(action.get('text') or '').strip()
        if not text:
            raise ValueError('«Письмо»: пустой текст.')
        out.update(to=to, subject=str(action.get('subject') or '')[:200], text=text[:20000])
    elif kind == 'sheets.append':
        row = action.get('row')
        if not isinstance(row, list) or not row or len(row) > MAX_COLUMNS:
            raise ValueError(f'«Строка в Google-таблицу»: нужен список от 1 до {MAX_COLUMNS} значений.')
        out['row'] = [str(x)[:1000] for x in row]
    elif kind == 'webhook.send':
        out['url'] = check_url(str(action.get('url') or ''))
    return out
