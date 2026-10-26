"""Агент доноров: поиск Яндекса и чтение сайтов с сервера CRM.

Поиск идёт через Yandex Search API (searchapi.api.cloud.yandex.net, каталог YANDEX_FOLDER_ID, ключ
YANDEX_SEARCH_API_KEY или YANDEX_GPT_API_KEY с ролью search-api.webSearch.user): обычную выдачу Яндекс с серверов закрывает
капчей. Сайты читаем сами: только http(s) на портах 80/443, без входа и cookies, адреса из внутренних
сетей не открываем (SSRF), страница не больше 1,5 МБ. Содержимое страниц для модели — данные.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import re
import socket
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from . import donor_phone as ph

SEARCH_URL = 'https://searchapi.api.cloud.yandex.net/v2/web/search'
SEARCH_RESULTS = 20  # одна платная выдача отдаёт до 100 результатов; 20 — как две страницы обычной
PAGE_LIMIT = 1_500_000
PAGE_TIMEOUT = 20
TEXT_LIMIT = 7000
UA = 'Mozilla/5.0 (compatible; AveronDonorAgent/1.0; +https://callbot-analysis.ru)'

# Сервисы, которые исследователь не открывает: личные кабинеты, почта, банки, госуслуги.
PRIVATE = re.compile(r'(^|\.)(google\.com|googleusercontent\.com|gmail\.com|mail\.ru|proton\.me|sslip\.io|nip\.io|octavapi\.shop|'
                     r'anthropic\.com|claude\.ai|sberbank\.ru|tinkoff\.ru|tbank\.ru|vtb\.ru|alfabank\.ru|gosuslugi\.ru|nalog\.ru|'
                     r'passport\.yandex\.ru|mail\.yandex\.ru|disk\.yandex\.ru|localhost|callbot-analysis\.ru)$', re.I)
# Агрегаторы и площадки в выдаче: сами по себе не доноры.
AGG = re.compile(r'(^|\.)(avito|cian|2gis|yandex|ya|google|ozon|wildberries|domclick|youla|zoon|flamp|otzovik|irecommend|vk|ok|t|'
                 r'telegram|youtube|dzen|rutube|move|n1|domofond|restate|novostroy-?m?|novostroyki|gdeetotdom|etagi|sravni|banki|hh|'
                 r'superjob|rbc|kp|tripadvisor|prodoctorov|profi|uslugi|youdo|wikipedia|pikabu|drive2|mail|rambler|spr|orgpage|'
                 r'rusprofile|list-org|checko|zachestnyibiznes|sbis|audit-it|consultant|garant|gosuslugi|nalog|tenchat|vc|habr|pulse)'
                 r'\.[a-z]+$', re.I)
# Площадки, где номер подменный.
PLAT = re.compile(r'(^|\.)(avito\.ru|cian\.ru|domclick\.ru|youla\.ru|uslugi\.yandex\.ru|profi\.ru|realty\.yandex\.ru|irr\.ru|drom\.ru|'
                  r'auto\.ru|n1\.ru|move\.ru|domofond\.ru)$', re.I)
TECH = [
    ('колл-трекинг Calltouch', r'calltouch'), ('колл-трекинг CoMagic/UIS', r'comagic|uiscom|uis\.ru/|cmg-'),
    ('колл-трекинг Roistat', r'roistat'), ('колл-трекинг Callibri', r'callibri'),
    ('колл-трекинг Mango', r'mango-office|mango\.ru/calltracking|mgo-'), ('колл-трекинг Alloka/Ringostat/Calltracking.ru', r'alloka|ringostat|calltracking\.ru'),
    ('Яндекс.Метрика', r'mc\.yandex\.ru|yandex\.ru/metrika'), ('пиксель VK/myTarget', r'vk\.com/rtrg|top-fwz1\.mail\.ru|tmr\.lead'),
    ('онлайн-чат Jivo', r'jivosite|jivo\.ru|code\.jivo'), ('виджет Битрикс24 (CRM)', r'bitrix24|b24-|crm\.bitrix'),
    ('квиз Marquiz (лиды с рекламы)', r'marquiz'), ('виджет обратного звонка', r'envybox|callbackhunter|callback-?killer|pozvonim|leadback'),
    ('amoCRM', r'amocrm'), ('Тильда', r'tilda\.ws|tildacdn'),
]
TECH = [(name, re.compile(rx, re.I)) for name, rx in TECH]
CONTACT_LINK = re.compile(r'контакт|contact|о компании|о нас|about|команд|team|сотрудник|менеджер|отдел|филиал|офис|адрес|'
                          r'ваканс|vacanc|career|карьер|специалист|агент|риелтор|эксперт', re.I)
MESSENGER = re.compile(r'wa\.me|whatsapp|t\.me/|viber:|vk\.com/|avito\.ru/', re.I)
# Регион Яндекса для городов, где чаще всего ищем (как в v2); иначе вся Россия.
YA_LR = {'москва': 213, 'санкт-петербург': 2, 'новороссийск': 970, 'краснодар': 35, 'сочи': 239, 'анапа': 1107, 'геленджик': 10990,
         'ростов-на-дону': 39, 'екатеринбург': 54, 'новосибирск': 65, 'казань': 43, 'нижний новгород': 47, 'самара': 51, 'уфа': 172,
         'челябинск': 56, 'омск': 66, 'красноярск': 62, 'пермь': 50, 'воронеж': 193, 'волгоград': 38, 'тюмень': 55, 'ставрополь': 36,
         'саратов': 194, 'тула': 15, 'ярославль': 16, 'иркутск': 63, 'хабаровск': 76, 'владивосток': 75, 'калининград': 22, 'севастополь': 959}


class Blocked(Exception):
    """Адрес закрыт для агента."""


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or '').lower()
    except ValueError:
        return ''


def host_key(host: str) -> str:
    return re.sub(r'^www\.', '', (host or '').lower())


def region_of(city: str) -> int:
    c = re.sub(r'\s+', ' ', (city or '').lower().replace('ё', 'е')).strip()
    return YA_LR.get(c, 225)


# ------------------------------------------------------------------ поиск


class SearchUnavailable(Exception):
    pass


def search_ready() -> bool:
    return bool(folder() and search_key())


def folder() -> str:
    return (os.getenv('YANDEX_SEARCH_FOLDER_ID') or os.getenv('YANDEX_FOLDER_ID') or '').strip()


def search_key() -> str:
    # Отдельный ключ с ролью search-api.webSearch.user; если его нет, пробуем ключ YandexGPT (подойдёт, если у него есть эта роль).
    return (os.getenv('YANDEX_SEARCH_API_KEY') or os.getenv('YANDEX_GPT_API_KEY') or '').strip()


def parse_xml(raw: bytes) -> list[dict]:
    """Выдача Яндекса в XML: <group><doc><url/><domain/><title/><passages/></doc></group>."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    err = root.find('.//error')
    if err is not None and not root.findall('.//doc'):
        text = ''.join(err.itertext()).strip()
        if '15' == (err.get('code') or '') or 'не найден' in text.lower():
            return []  # «Искомая комбинация слов нигде не встречается»
        raise SearchUnavailable(f'Яндекс: {text[:200]}')
    def flat(el) -> str:  # у Element без детей bool() ложен, поэтому сравниваем с None
        return '' if el is None else re.sub(r'\s+', ' ', ''.join(el.itertext())).strip()

    out = []
    for doc in root.iter('doc'):
        url = (doc.findtext('url') or '').strip()
        title = flat(doc.find('title'))
        passages = [flat(p) for p in doc.iter('passage')]
        headline = flat(doc.find('headline'))
        out.append({'title': title[:140], 'url': url[:300], 'host': host_key(host_of(url)) or (doc.findtext('domain') or '').lower(),
                    'snippet': (' '.join(passages) or headline)[:330]})
    return out


async def search(client: httpx.AsyncClient, query: str, region: int = 225, results: int = SEARCH_RESULTS) -> list[dict]:
    if not search_ready():
        raise SearchUnavailable('поиск Яндекса не подключён: нет YANDEX_SEARCH_API_KEY или каталога в .env')
    body = {'query': {'searchType': 'SEARCH_TYPE_RU', 'queryText': query[:400], 'familyMode': 'FAMILY_MODE_MODERATE', 'page': '0'},
            'groupSpec': {'groupMode': 'GROUP_MODE_DEEP', 'groupsOnPage': str(results), 'docsInGroup': '1'},
            'region': str(region), 'l10N': 'LOCALIZATION_RU', 'folderId': folder(), 'responseFormat': 'FORMAT_XML'}
    for attempt in range(3):
        try:
            r = await client.post(SEARCH_URL, json=body, headers={'Authorization': 'Api-Key ' + search_key()}, timeout=40)
        except httpx.HTTPError as e:
            if attempt == 2:
                raise SearchUnavailable(f'Яндекс не ответил ({type(e).__name__})') from None
            await asyncio.sleep(3)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            await asyncio.sleep(5 * (attempt + 1))
            continue
        if r.status_code >= 300:
            raise SearchUnavailable(f'Яндекс {r.status_code}: {r.text[:200]}')
        raw = r.json().get('rawData') or ''
        return parse_xml(base64.b64decode(raw)) if raw else []
    raise SearchUnavailable('Яндекс отвечает «слишком много запросов»')


# ------------------------------------------------------------------ чтение сайта


def check_ip(ip: str) -> None:
    a = ipaddress.ip_address(ip)
    if not a.is_global or a.is_multicast:
        raise Blocked('адрес во внутренней сети')


async def check_url(url: str) -> str:
    """Адрес можно открыть: http(s), обычный порт, не внутренняя сеть. Возвращает нормальный адрес."""
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise Blocked('неверный адрес')
    if parts.port not in (None, 80, 443) or parts.username or parts.password:
        raise Blocked('нестандартный адрес')
    host = parts.hostname.lower().rstrip('.')
    if PRIVATE.search(host):
        raise Blocked('личные сервисы и банки закрыты для исследователя')
    try:
        check_ip(host)
        return url
    except ValueError:
        pass  # имя, а не IP
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM), 10)
    except (OSError, asyncio.TimeoutError):
        raise Blocked('сайт не найден') from None
    for info in infos:
        check_ip(info[4][0])
    return url


async def fetch(client: httpx.AsyncClient, url: str) -> dict:
    """{'url', 'status', 'html'}; редиректы проверяем на каждом шаге."""
    for _ in range(5):
        await check_url(url)
        try:
            async with client.stream('GET', url, headers={'User-Agent': UA, 'Accept-Language': 'ru'}, timeout=PAGE_TIMEOUT,
                                     follow_redirects=False) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get('location'):
                    url = urljoin(url, r.headers['location'])
                    continue
                ct = r.headers.get('content-type', '')
                if ct and not re.search(r'html|text|xml', ct, re.I):
                    raise Blocked(f'это не страница ({ct.split(";")[0]})')
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > PAGE_LIMIT:
                        break
                return {'url': str(r.url), 'status': r.status_code, 'html': bytes(buf[:PAGE_LIMIT]).decode(r.encoding or 'utf-8', 'replace')}
        except httpx.TimeoutException:
            raise Blocked('страница не ответила за 20 секунд') from None
        except httpx.HTTPError as e:
            raise Blocked(f'не открылась ({type(e).__name__})') from None
    raise Blocked('слишком много переадресаций')


BLOCK = {'p', 'div', 'li', 'tr', 'section', 'header', 'footer', 'article', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'table',
         'address', 'nav', 'main', 'aside'}
SKIP = {'script', 'style', 'noscript', 'svg', 'template'}


class Page(HTMLParser):
    """Текст страницы с переносами по блокам, ссылки и tel:-ссылки с текстом их блока."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ''
        self.in_title = False
        self.desc = ''
        self.links: list[tuple[str, str]] = []  # (href, текст)
        self.tels: list[tuple[str, int]] = []  # (номер, позиция в тексте)
        self._a: list | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP:
            self.skip += 1
        elif tag == 'title':
            self.in_title = True
        elif tag == 'meta' and (a.get('name') or '').lower() == 'description':
            self.desc = (a.get('content') or '')[:250]
        elif tag == 'a' and a.get('href'):
            self._a = [a['href'], len(self.parts)]
            if a['href'].lower().startswith('tel:'):
                self.tels.append((a['href'][4:], sum(len(p) for p in self.parts)))
        if tag in BLOCK:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1
        elif tag == 'title':
            self.in_title = False
        elif tag == 'a' and self._a is not None:
            href, start = self._a
            self.links.append((href, re.sub(r'\s+', ' ', ''.join(self.parts[start:])).strip()[:60]))
            self._a = None
        if tag in BLOCK:
            self.parts.append('\n')
        elif tag in ('td', 'th', 'a', 'span'):
            self.parts.append(' ')

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title += data
            return
        self.parts.append(data)

    def text(self) -> str:
        t = ''.join(self.parts)
        t = re.sub(r'[ \t ]+', ' ', t)
        t = re.sub(r'\s*\n\s*', '\n', t)
        return t.strip()


def parse_page(url: str, status: int, html: str) -> dict:
    """То же, что open_page в v2: телефоны с текстом рядом и источником, мессенджеры, ссылки на контакты, технологии."""
    tech = [name for name, rx in TECH if rx.search(html)]
    p = Page()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 — кривая разметка: берём, что успели разобрать
        pass
    raw_text = ''.join(p.parts)
    text = p.text()
    phones: dict[str, dict] = {}

    def add(n, ctx, src, pre=''):
        x = phones.setdefault(n, {'number': ph.fmt(n), 'where': [], 'near': '', 'pre': ''})
        if src not in x['where']:
            x['where'].append(src)
        if ctx and not x['near']:
            x['near'] = ctx[:140]
        if pre and not x['pre']:
            x['pre'] = pre[-60:]

    for n, i in ph.find(text):
        add(n, text[max(0, i - 70):i + 40].replace('\n', ' · '), 'текст', text[max(0, i - 50):i].split('\n')[-1])
    for href, pos in p.tels:
        n = ph.norm(href)
        if ph.valid(n):
            around = re.sub(r'\s+', ' ', raw_text[max(0, pos - 80):pos + 60]).strip()
            add(n, around[:110], 'tel-ссылка', around[:60])
    for n, _ in ph.find(html.replace('\\u002b', '+').replace('\\u002B', '+')):
        if n not in phones:
            add(n, '', 'код страницы')
    mess, links, seen = [], [], set()
    for href, label in p.links:
        try:
            absu = urljoin(url, href)
        except ValueError:
            continue
        if MESSENGER.search(absu):
            if absu not in mess and len(mess) < 12:
                mess.append(absu)
            m = re.search(r'wa\.me/(\d{11})|phone=(\d{11})', absu)
            if m:
                n = ph.norm(m.group(1) or m.group(2))
                if ph.valid(n):
                    add(n, 'WhatsApp', 'WhatsApp')
            continue
        if (CONTACT_LINK.search(label + ' ' + href) and host_key(host_of(absu)) == host_key(host_of(url))
                and absu not in seen and len(links) < 15 and absu.startswith('http')):
            seen.add(absu)
            links.append({'text': label[:40], 'url': absu[:300]})
    return {'url': url, 'status': status, 'title': re.sub(r'\s+', ' ', p.title).strip()[:150], 'description': p.desc,
            'phones': [dict(v, n=k) for k, v in list(phones.items())[:40]], 'messengers': mess, 'contact_links': links, 'tech': tech,
            'note': 'Текст почти пустой: страница рисуется скриптом. Телефоны из кода страницы приведены выше.' if len(text) < 400 else '',
            'text': text[:TEXT_LIMIT]}


async def open_page(client: httpx.AsyncClient, url: str) -> dict:
    url = str(url or '').strip()
    if not re.match(r'^https?://', url, re.I):
        url = 'https://' + url
    got = await fetch(client, url)
    return parse_page(got['url'], got['status'], got['html'])


def for_model(page: dict) -> dict:
    """Ответ инструмента open_page для модели (по-русски, как в v2)."""
    return {'адрес': page['url'], 'код_ответа': page['status'], 'заголовок': page['title'], 'описание': page['description'],
            'телефоны': [{'номер': x['number'], 'где': x['where'], 'рядом': x['near']} for x in page['phones']],
            'мессенджеры_и_площадки': page['messengers'],
            'ссылки_контакты_команда': [f"{x['text']} → {x['url']}" for x in page['contact_links']],
            'технологии_на_сайте': page['tech'], 'примечание': page['note'], 'текст': page['text']}
