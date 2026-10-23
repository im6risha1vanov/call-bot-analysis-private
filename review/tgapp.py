"""Ключи приложения Telegram (api_id, api_hash) с my.telegram.org прямо с сервера.

Нужно, когда сайт не пускает с компьютера («Too many attempts»): запросы уходят с адреса сервера.
Шаги те же, что на сайте: номер → код из Telegram (приходит в чат «Telegram» этого номера) →
страница /apps; если приложения ещё нет, создаём его и читаем ключи. Номер может быть любым:
ключи принадлежат приложению, а войдёт бот всё равно в рабочий аккаунт (tgchats login).
"""
from __future__ import annotations

import html
import re
import secrets

import httpx

BASE = 'https://my.telegram.org'
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/124.0 Safari/537.36',
           'X-Requested-With': 'XMLHttpRequest'}


class TgAppError(Exception):
    pass


def site_phone(text: str) -> str:
    """«8 951 727-22-86» → «+79517272286»: сайт ждёт номер в международном виде."""
    digits = re.sub(r'\D', '', text)
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if not 10 < len(digits) < 16:
        raise TgAppError('номер нужен целиком, например +79517272286')
    return '+' + digits


def parse_keys(page: str) -> tuple[str, str] | None:
    """Ключи со страницы /apps: «App api_id: <strong>123</strong>», «App api_hash: <32 hex>»."""
    text = re.sub(r'<[^>]+>', ' ', page)
    api_id = re.search(r'api_id:?\s+(\d{3,12})\b', text)
    api_hash = re.search(r'api_hash:?\s+([0-9a-f]{32})\b', text)
    return (api_id[1], api_hash[1]) if api_id and api_hash else None


def form_hash(page: str) -> str | None:
    """Скрытое поле hash формы «Create new application»."""
    found = (re.search(r'name="hash"[^>]*?value="([^"]+)"', page)
             or re.search(r'value="([^"]+)"[^>]*?name="hash"', page))
    return html.unescape(found[1]) if found else None


def fetch_keys(phone: str, ask_code, client: httpx.Client | None = None,
               title: str = 'Averon CRM') -> tuple[str, str]:
    """Вход на my.telegram.org с номером phone; ask_code() спрашивает код из Telegram. Возвращает (api_id, api_hash)."""
    own = client is None
    client = client or httpx.Client(base_url=BASE, headers=HEADERS, timeout=30, follow_redirects=True)
    try:
        r = client.post('/auth/send_password', data={'phone': phone})
        try:
            random_hash = r.json()['random_hash']
        except (ValueError, KeyError, TypeError):
            raise TgAppError(f'my.telegram.org не отправил код: {r.text.strip()[:200]}') from None
        # Ответ «true» и кука stel_token — вход выполнен, куку httpx хранит сам.
        r = client.post('/auth/login', data={'phone': phone, 'random_hash': random_hash, 'password': ask_code()})
        if r.text.strip() != 'true':
            raise TgAppError(f'код не подошёл: {r.text.strip()[:200]}')
        page = client.get('/apps').text
        keys = parse_keys(page)
        if keys:
            return keys
        token = form_hash(page)
        if not token:
            raise TgAppError('на странице /apps нет ни ключей, ни формы создания приложения')
        r = client.post('/apps/create', data={'hash': token, 'app_title': title,
                                              'app_shortname': 'averoncrm' + secrets.token_hex(2),
                                              'app_url': '', 'app_platform': 'desktop', 'app_desc': ''})
        keys = parse_keys(client.get('/apps').text)
        if not keys:
            raise TgAppError(f'приложение не создалось: {r.text.strip()[:200]}')
        return keys
    except httpx.HTTPError as exc:
        raise TgAppError(f'нет связи с my.telegram.org: {exc}') from None
    finally:
        if own:
            client.close()
