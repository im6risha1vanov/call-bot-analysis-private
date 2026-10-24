"""Конструктор автоматизаций: сценарии «событие → условия → действия» для компании.

Сценарий хранится JSON-ом (automations.spec):
    {"trigger": {"type": "deal.paid"},
     "if": [{"field": "deal.amount", "op": ">=", "value": 50000}],
     "actions": [{"type": "telegram.send", "account": 3, "text": "Оплата {deal.amount} ₽ от {deal.client}"}]}

Откуда события: строки сделок, номеров, итогов звонков и разборов кладёт в очередь
automation_events триггер базы (migrations/030_automations.sql); входящий вебхук
/hook/<token> и расписание кладёт сам движок. Цикл loop() в процессе сайта разбирает
очередь, проверяет условия, выполняет действия (crm/automation_blocks.py), пишет
запуск в automation_runs и повторяет временные ошибки до трёх раз.

Включает сценарий только человек; движок сам выключает его, если тот срабатывает
слишком часто (защита от зацикливания вебхуков).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from crypto_util import decrypt, encrypt
from . import automation_blocks as blocks

log = logging.getLogger(__name__)
POLL_SEC = 5
BATCH = 50
RETRY_AFTER = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30)]
MAX_ATTEMPTS = len(RETRY_AFTER)
RATE_WINDOW = timedelta(minutes=1)
RATE_MAX = 30  # запусков одного сценария в минуту, больше — выключаем
MAX_ACTIONS = 10
MAX_CONDITIONS = 10
MAX_HOOK_BYTES = 64_000
CODE_TTL = timedelta(hours=24)
TG_PREFIX = 'aut_'
TG_CODE = re.compile(r'aut_([A-Za-z0-9]{12,40})')

DEAL_KINDS = {'presentation': 'Проведена презентация', 'materials': 'Отправлены материалы',
              'contract_sent': 'Отправлен договор', 'contract_edits': 'Клиент прислал правки',
              'contract_signed': 'Подписан договор', 'paid': 'Оплата (продажа)', 'lost': 'Отказ',
              'reopen': 'Сделка снова в работе', 'note': 'Заметка по сделке'}
DEAL_SAMPLE = {'deal': {'phone': '79990000000', 'client': 'ООО Пример', 'manager': 'Иван Петров',
                        'event': 'Оплата получена', 'amount': 150000, 'reason': '', 'note': 'первый платёж',
                        'author': 'Директор'}}
TRIGGERS = {
    **{f'deal.{k}': {'title': v, 'group': 'Сделки', 'sample': DEAL_SAMPLE} for k, v in DEAL_KINDS.items()},
    'lead.added': {'title': 'Новый номер в базе', 'group': 'База',
                   'sample': {'lead': {'phone': '79990000000', 'name': 'Анна', 'company': 'ООО Пример',
                                       'source': 'ГЦК', 'note': '', 'manager': 'Иван Петров'}}},
    'call.outcome': {'title': 'Менеджер отметил итог звонка', 'group': 'Звонки',
                     'sample': {'call': {'phone': '79990000000', 'manager': 'Иван Петров', 'outcome': 'Назначил встречу',
                                         'outcome_code': 'meeting', 'comment': 'встреча в пятницу', 'due': '2026-10-30'}}},
    'call.reviewed': {'title': 'Звонок разобран', 'group': 'Звонки',
                      'sample': {'call': {'phone': '79990000000', 'manager': 'Иван Петров', 'success': True,
                                          'result': 'успех', 'category': '', 'chance': 'высокий',
                                          'started': '2026-10-24 12:30', 'duration': 245}}},
    'schedule.daily': {'title': 'Каждый день в заданное время', 'group': 'Расписание',
                       'sample': {'date': '2026-10-24', 'day': {'reviewed': 18, 'successful': 4, 'leads_added': 120,
                                                                'outcomes': 35, 'paid': 1, 'paid_sum': 165000}}},
    'webhook.in': {'title': 'Входящий вебхук от другой системы', 'group': 'Другие системы',
                   'sample': {'data': {'example': 'поля, которые пришлёт ваша система'}}},
}
OPS = {
    '=': lambda a, b: norm(a) == norm(b), '!=': lambda a, b: norm(a) != norm(b),
    '>': lambda a, b: num(a) is not None and num(b) is not None and num(a) > num(b),
    '>=': lambda a, b: num(a) is not None and num(b) is not None and num(a) >= num(b),
    '<': lambda a, b: num(a) is not None and num(b) is not None and num(a) < num(b),
    '<=': lambda a, b: num(a) is not None and num(b) is not None and num(a) <= num(b),
    'contains': lambda a, b: str(b).lower() in str(a if a is not None else '').lower(),
    'empty': lambda a, b: a in (None, '', [], {}),
    'not_empty': lambda a, b: a not in (None, '', [], {}),
}
FIELD = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\}')
TIME = re.compile(r'([01]?\d|2[0-3]):([0-5]\d)')


# --- Значения, условия, шаблоны -------------------------------------------------------------------

def num(value):
    if isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(' ', '').replace(',', '.'))
    except (TypeError, ValueError):
        return None


def norm(value):
    n = num(value)
    return n if n is not None else str(value if value is not None else '').strip().lower()


def get(payload: dict, path: str):
    value = payload
    for part in path.split('.'):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def show(value) -> str:
    """Значение для текста: 150000 → «150 000», True → «да»."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'да' if value else 'нет'
    if isinstance(value, (int, float, Decimal)):
        n = float(value)
        text = f'{n:,.0f}' if n == int(n) else f'{n:,.2f}'
        return text.replace(',', ' ')
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render(template: str, payload: dict) -> str:
    return FIELD.sub(lambda m: show(get(payload, m[1])), template)


def matches(conditions: list[dict], payload: dict) -> bool:
    return all(OPS[c['op']](get(payload, c['field']), c.get('value')) for c in conditions)


def plain(value):
    """JSON без Decimal и дат: так событие уходит в вебхук и в базу."""
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


# --- Проверка сценария ----------------------------------------------------------------------------

def validate(spec, accounts: dict[int, dict]) -> dict:
    """Проверенная копия сценария. Всё лишнее отбрасывается; ошибки — ValueError по-русски."""
    if not isinstance(spec, dict):
        raise ValueError('Сценарий описан неверно.')
    trigger = spec.get('trigger') or {}
    kind = trigger.get('type') if isinstance(trigger, dict) else None
    if kind not in TRIGGERS:
        raise ValueError(f'Неизвестное событие: {kind}. Есть: {", ".join(TRIGGERS)}.')
    clean_trigger = {'type': kind}
    if kind == 'schedule.daily':
        m = TIME.fullmatch(str(trigger.get('time') or '').strip())
        if not m:
            raise ValueError('Для расписания нужно время ЧЧ:ММ, например 19:00.')
        clean_trigger['time'] = f'{int(m[1]):02d}:{m[2]}'
    conditions = spec.get('if') or []
    if not isinstance(conditions, list) or len(conditions) > MAX_CONDITIONS:
        raise ValueError(f'Условий может быть до {MAX_CONDITIONS}.')
    clean_if = []
    for c in conditions:
        if not isinstance(c, dict) or c.get('op') not in OPS or not FIELD.fullmatch('{%s}' % c.get('field', '')):
            raise ValueError(f'Условие описано неверно. Операции: {", ".join(OPS)}.')
        item = {'field': c['field'], 'op': c['op']}
        if c['op'] not in ('empty', 'not_empty'):
            if c.get('value') is None or isinstance(c.get('value'), (dict, list)):
                raise ValueError(f'В условии по «{c["field"]}» нет значения.')
            item['value'] = c['value']
        clean_if.append(item)
    actions = spec.get('actions') or []
    if not isinstance(actions, list) or not actions:
        raise ValueError('Нужно хотя бы одно действие.')
    if len(actions) > MAX_ACTIONS:
        raise ValueError(f'Действий может быть до {MAX_ACTIONS}.')
    return {'trigger': clean_trigger, 'if': clean_if, 'actions': [blocks.check_action(a, accounts) for a in actions]}


def describe(spec: dict, accounts: dict[int, dict]) -> str:
    """Сценарий человеческим языком: так его пересказывает агент и показывает список."""
    t = spec['trigger']
    head = TRIGGERS[t['type']]['title'] + (f' в {t["time"]}' if t.get('time') else '')
    conds = [f'{c["field"]} {c["op"]} {c.get("value", "")}'.strip() for c in spec.get('if', [])]
    acts = []
    for a in spec['actions']:
        title = blocks.ACTIONS[a['type']]['title']
        if a.get('account') in accounts:
            title += f' ({accounts[a["account"]]["title"]})'
        elif a['type'] == 'email.send':
            title += f' ({", ".join(a["to"])})'
        acts.append(title)
    return f'Когда: {head}' + (f'; если {" и ".join(conds)}' if conds else '') + f'; делаем: {"; ".join(acts)}.'


# --- Аккаунты -------------------------------------------------------------------------------------

def account_view(row) -> dict:
    return {'id': row['id'], 'kind': row['kind'], 'title': row['title'], 'status': row['status'], 'error': row['error']}


async def accounts(pool, client_id: int) -> dict[int, dict]:
    rows = await pool.fetch('SELECT * FROM automation_accounts WHERE client_id=$1 ORDER BY id', client_id)
    return {r['id']: account_view(r) for r in rows}


async def account_secret(pool, client_id: int, account_id: int | None) -> dict | None:
    if account_id is None:
        return None
    row = await pool.fetchrow("SELECT data_enc, status FROM automation_accounts WHERE id=$1 AND client_id=$2",
                              account_id, client_id)
    if row is None or row['status'] != 'ready' or not row['data_enc']:
        return None
    return json.loads(decrypt(row['data_enc']))


async def telegram_link(pool, client_id: int, author: str | None) -> dict:
    """Ссылки на бота: личный чат и группа. Чат подключится, когда в нём нажмут «Старт»."""
    from .telegram import bot_username
    name = await bot_username()
    if not name:
        raise ValueError('У CRM нет бота Telegram.')
    code = secrets.token_urlsafe(18).replace('-', 'a').replace('_', 'b')
    row = await pool.fetchrow(
        """INSERT INTO automation_accounts(client_id, kind, title, connect_code, created_by)
           VALUES($1, 'telegram', 'Telegram: ждём подключения', $2, $3) RETURNING id""", client_id, code, author)
    return {'id': row['id'], 'private': f'https://t.me/{name}?start={TG_PREFIX}{code}',
            'group': f'https://t.me/{name}?startgroup={TG_PREFIX}{code}'}


def telegram_code(text: str | None) -> str | None:
    """Код из «/start aut_xxx» (в группе «/start@bot aut_xxx»)."""
    parts = (text or '').split(maxsplit=1)
    m = TG_CODE.fullmatch(parts[1].strip()) if len(parts) == 2 else None
    return m[1] if m else None


async def bind_telegram(pool, code: str, chat_id: int, title: str) -> str:
    """Бот увидел /start с кодом: привязываем чат. Возвращает ответ для чата."""
    row = await pool.fetchrow(
        """UPDATE automation_accounts SET data_enc=$2, title=$3, status='ready', connect_code=NULL, error=NULL, updated_at=now()
           WHERE connect_code=$1 AND kind='telegram' AND created_at > now() - $4::interval RETURNING id""",
        code, encrypt(json.dumps({'chat_id': chat_id})), ('Telegram: ' + (title or str(chat_id)))[:120], CODE_TTL)
    if row is None:
        return 'Ссылка для подключения устарела или уже использована. Попросите новую в CRM.'
    return 'Готово: этот чат подключён к автоматизациям CRM. Уведомления будут приходить сюда.'


async def connect_sheet(pool, client_id: int, url: str, sheet: str | None, author: str | None) -> dict:
    spreadsheet = blocks.sheet_id(url)
    if not spreadsheet:
        raise ValueError('Не похоже на ссылку Google-таблицы.')
    try:
        info = await blocks.sheets_probe(spreadsheet)
    except (blocks.Fail, blocks.Retry) as exc:
        raise ValueError(str(exc))
    sheet = (sheet or '').strip() or info['sheets'][0]
    if sheet not in info['sheets']:
        raise ValueError(f'Листа «{sheet}» нет. Есть: {", ".join(info["sheets"])}.')
    row = await pool.fetchrow(
        """INSERT INTO automation_accounts(client_id, kind, title, data_enc, status, created_by)
           VALUES($1, 'sheets', $2, $3, 'ready', $4) RETURNING *""",
        client_id, f'{info["title"]} / {sheet}'[:120], encrypt(json.dumps({'spreadsheet_id': spreadsheet, 'sheet': sheet})),
        author)
    return account_view(row)


async def delete_account(pool, client_id: int, account_id: int) -> dict:
    used = [r['name'] for r in await pool.fetch(
        """SELECT name FROM automations WHERE client_id=$1
           AND EXISTS (SELECT 1 FROM jsonb_array_elements(spec->'actions') a WHERE (a->>'account')::bigint = $2)""",
        client_id, account_id)]
    if used:
        raise ValueError(f'Подключение используется в сценариях: {", ".join(used)}. Сначала поменяйте их.')
    await pool.execute('DELETE FROM automation_accounts WHERE id=$1 AND client_id=$2', account_id, client_id)
    return {'ok': True}


# --- Сценарии -------------------------------------------------------------------------------------

def automation_view(row, accs: dict[int, dict]) -> dict:
    spec = json.loads(row['spec']) if isinstance(row['spec'], str) else row['spec']
    out = {'id': row['id'], 'name': row['name'], 'enabled': row['enabled'], 'spec': spec,
           'text': describe(spec, accs), 'paused_reason': row['paused_reason']}
    if spec['trigger']['type'] == 'webhook.in':
        out['hook'] = f'/hook/{row["hook_token"]}'
    return out


async def overview(pool, client_id: int) -> dict:
    accs = await accounts(pool, client_id)
    rows = await pool.fetch('SELECT * FROM automations WHERE client_id=$1 ORDER BY id', client_id)
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    runs = await pool.fetchval('SELECT runs FROM automation_usage WHERE client_id=$1 AND month=$2', client_id, month)
    lim = await pool.fetchrow('SELECT * FROM automation_limits WHERE client_id=$1', client_id)
    return {'automations': [automation_view(r, accs) for r in rows], 'accounts': list(accs.values()),
            'usage': {'month': month, 'runs': runs or 0, 'active': sum(r['enabled'] for r in rows),
                      'max_runs_month': lim and lim['max_runs_month'], 'max_active': lim and lim['max_active']},
            'catalog': catalog()}


def catalog() -> dict:
    return {'triggers': {k: {'title': v['title'], 'group': v['group'], 'fields': sorted(fields(v['sample']))}
                         for k, v in TRIGGERS.items()},
            'actions': {k: {x: v[x] for x in ('title', 'about', 'account', 'fields')} for k, v in blocks.ACTIONS.items()},
            'ops': list(OPS), 'email_ready': blocks.smtp_ready(), 'proxy_ready': bool(blocks.proxy_url()),
            'sheets_email': blocks.service_email()}


def fields(sample: dict, prefix: str = '') -> list[str]:
    out = []
    for k, v in sample.items():
        path = f'{prefix}{k}'
        out += fields(v, path + '.') if isinstance(v, dict) else [path]
    return out


async def save(pool, client_id: int, automation_id: int | None, name: str, spec, author: str | None) -> dict:
    name = (name or '').strip()[:120]
    if not name:
        raise ValueError('Дайте сценарию название.')
    accs = await accounts(pool, client_id)
    clean = validate(spec, accs)
    token = secrets.token_urlsafe(24) if clean['trigger']['type'] == 'webhook.in' else None
    if automation_id is None:
        row = await pool.fetchrow(
            """INSERT INTO automations(client_id, name, spec, hook_token, created_by) VALUES($1,$2,$3,$4,$5) RETURNING *""",
            client_id, name, json.dumps(clean, ensure_ascii=False), token, author)
    else:
        # правка выключает сценарий: снова включает человек, посмотрев пробный запуск
        row = await pool.fetchrow(
            """UPDATE automations SET name=$3, spec=$4, enabled=false, paused_reason=NULL, updated_at=now(),
                      hook_token=CASE WHEN $5::text IS NULL THEN NULL ELSE coalesce(hook_token, $5) END
               WHERE id=$1 AND client_id=$2 RETURNING *""",
            automation_id, client_id, name, json.dumps(clean, ensure_ascii=False), token)
        if row is None:
            raise ValueError('Такого сценария нет.')
    return automation_view(row, accs)


async def set_enabled(pool, client_id: int, automation_id: int, on: bool) -> dict:
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow('SELECT * FROM automations WHERE id=$1 AND client_id=$2 FOR UPDATE', automation_id, client_id)
        if row is None:
            raise ValueError('Такого сценария нет.')
        if on and not row['enabled']:
            limit = await conn.fetchval('SELECT max_active FROM automation_limits WHERE client_id=$1', client_id)
            active = await conn.fetchval('SELECT count(*) FROM automations WHERE client_id=$1 AND enabled', client_id)
            if limit is not None and active >= limit:
                raise ValueError(f'По тарифу можно до {limit} включённых сценариев.')
            if not await conn.fetchval('SELECT 1 FROM automation_runs WHERE automation_id=$1 AND test AND status=$2 LIMIT 1',
                                       automation_id, 'ok'):
                raise ValueError('Сначала сделайте пробный запуск: проверьте, что сообщение выглядит как надо.')
        last = row['last_fired']
        spec = json.loads(row['spec']) if isinstance(row['spec'], str) else row['spec']
        if on and spec['trigger']['type'] == 'schedule.daily':
            # включили после времени срабатывания: первая отправка завтра, а не сразу
            tz = ZoneInfo(await conn.fetchval('SELECT timezone FROM clients WHERE id=$1', client_id) or 'Europe/Moscow')
            local = datetime.now(tz)
            if local.strftime('%H:%M') >= spec['trigger']['time']:
                last = local.date().isoformat()
        row = await conn.fetchrow(
            'UPDATE automations SET enabled=$2, paused_reason=NULL, last_fired=$3, updated_at=now() WHERE id=$1 RETURNING *',
            automation_id, on, last)
    return automation_view(row, await accounts(pool, client_id))


async def delete(pool, client_id: int, automation_id: int) -> dict:
    await pool.execute('DELETE FROM automations WHERE id=$1 AND client_id=$2', automation_id, client_id)
    return {'ok': True}


async def runs(pool, client_id: int, automation_id: int, limit: int = 30) -> list[dict]:
    rows = await pool.fetch(
        """SELECT id, test, status, steps, attempts, created_at FROM automation_runs
           WHERE automation_id=$1 AND client_id=$2 ORDER BY id DESC LIMIT $3""", automation_id, client_id, limit)
    return [{'id': r['id'], 'test': r['test'], 'status': r['status'], 'attempts': r['attempts'],
             'steps': json.loads(r['steps']), 'at': r['created_at'].isoformat()} for r in rows]


# --- Данные события -------------------------------------------------------------------------------

async def manager_name(pool, client_id: int, ext) -> str:
    if not ext:
        return ''
    return await pool.fetchval('SELECT full_name FROM employees WHERE client_id=$1 AND extension=$2 LIMIT 1',
                               client_id, str(ext)) or f'Доб. {ext}'


async def lead_card(pool, client_id: int, phone: str) -> dict:
    """Имя и компания клиента из базы номеров (последние 10 цифр совпадают)."""
    row = await pool.fetchrow(
        """SELECT name, company FROM crm_leads WHERE client_id=$1 AND right(regexp_replace(number, '\\D', '', 'g'), 10)=$2
           ORDER BY id DESC LIMIT 1""", client_id, phone[-10:]) if phone else None
    return {'name': row['name'] or '', 'company': row['company'] or ''} if row else {'name': '', 'company': ''}


async def build_payload(pool, client_id: int, kind: str, ref_id: int | None) -> dict | None:
    """Поля события по строке-источнику. None — строки уже нет."""
    from .store import OUTCOMES
    if kind.startswith('deal.'):
        r = await pool.fetchrow('SELECT * FROM deal_events WHERE id=$1 AND client_id=$2', ref_id, client_id)
        if r is None:
            return None
        from .deals import EVENT_LABELS, LOST_REASONS
        card = await lead_card(pool, client_id, r['phone'])
        return {'deal': {'phone': r['phone'], 'client': card['company'] or card['name'], 'contact': card['name'],
                         'manager': await manager_name(pool, client_id, r['extension']),
                         'event': EVENT_LABELS.get(r['kind'], r['kind']), 'amount': r['amount'],
                         'reason': LOST_REASONS.get(r['reason'] or '', r['reason'] or ''), 'note': r['note'] or '',
                         'author': r['author_name'] or '', 'at': r['created_at']}}
    if kind == 'lead.added':
        r = await pool.fetchrow('SELECT * FROM crm_leads WHERE id=$1 AND client_id=$2', ref_id, client_id)
        if r is None:
            return None
        return {'lead': {'phone': r['number'], 'name': r['name'] or '', 'company': r['company'] or '',
                         'source': r['source'] or '', 'note': r['note'] or '',
                         'manager': await manager_name(pool, client_id, r['extension'])}}
    if kind == 'call.outcome':
        r = await pool.fetchrow('SELECT * FROM crm_outcomes WHERE id=$1 AND client_id=$2', ref_id, client_id)
        if r is None:
            return None
        card = await lead_card(pool, client_id, r['number'])
        return {'call': {'phone': r['number'], 'client': card['company'] or card['name'],
                         'manager': await manager_name(pool, client_id, r['extension']),
                         'outcome': OUTCOMES.get(r['outcome'], r['outcome']), 'outcome_code': r['outcome'],
                         'comment': r['comment'] or '', 'due': r['due_date']}}
    if kind == 'call.reviewed':
        r = await pool.fetchrow(
            """SELECT r.success, r.category, r.chance, r.extension, c.client_number, c.call_started_at,
                      c.duration_seconds FROM call_reviews r JOIN calls c ON c.id=r.call_id
               WHERE r.call_id=$1 AND r.client_id=$2""", ref_id, client_id)
        if r is None:
            return None
        from review.render import CATEGORY_LABELS
        tz = ZoneInfo(await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', client_id) or 'Europe/Moscow')
        return {'call': {'phone': r['client_number'] or '', 'manager': await manager_name(pool, client_id, r['extension']),
                         'success': r['success'], 'result': 'успех' if r['success'] else 'без результата',
                         'category': CATEGORY_LABELS.get(r['category'] or '', r['category'] or ''), 'chance': r['chance'] or '',
                         'started': r['call_started_at'].astimezone(tz).strftime('%Y-%m-%d %H:%M') if r['call_started_at'] else '',
                         'duration': r['duration_seconds']}}
    return None


async def day_stats(pool, client_id: int, tz: ZoneInfo, day: date) -> dict:
    start = datetime.combine(day, datetime.min.time(), tz)
    end = start + timedelta(days=1)
    q = lambda sql: pool.fetchrow(sql, client_id, start, end)  # noqa: E731
    reviews = await q('SELECT count(*) n, count(*) FILTER (WHERE success) ok FROM call_reviews '
                      'WHERE client_id=$1 AND created_at >= $2 AND created_at < $3')
    leads = await q('SELECT count(*) n FROM crm_leads WHERE client_id=$1 AND created_at >= $2 AND created_at < $3')
    outcomes = await q('SELECT count(*) n FROM crm_outcomes WHERE client_id=$1 AND created_at >= $2 AND created_at < $3')
    paid = await q("SELECT count(*) n, coalesce(sum(amount), 0) s FROM deal_events WHERE client_id=$1 AND kind='paid' "
                   "AND created_at >= $2 AND created_at < $3")
    return {'reviewed': reviews['n'], 'successful': reviews['ok'], 'leads_added': leads['n'],
            'outcomes': outcomes['n'], 'paid': paid['n'], 'paid_sum': paid['s']}


# --- Выполнение -----------------------------------------------------------------------------------

async def run_actions(pool, client_id: int, spec: dict, payload: dict, event: str, only: set[int] | None = None) -> list[dict]:
    """Действия по порядку; ошибка одного не останавливает остальные."""
    steps = []
    for i, a in enumerate(spec['actions']):
        if only is not None and i not in only:
            continue
        step = {'i': i, 'type': a['type'], 'ok': False}
        try:
            account = await account_secret(pool, client_id, a.get('account'))
            if a['type'] == 'telegram.send':
                step['preview'] = await blocks.telegram_send(a, account, render(a['text'], payload))
            elif a['type'] == 'email.send':
                step['preview'] = await blocks.email_send(a, render(a['subject'], payload), render(a['text'], payload))
            elif a['type'] == 'sheets.append':
                step['preview'] = await blocks.sheets_append(a, account, [render(x, payload) for x in a['row']])
            elif a['type'] == 'webhook.send':
                step['preview'] = await blocks.webhook_send(a, event, payload)
            step['ok'] = True
        except blocks.Retry as exc:
            step.update(error=str(exc), retry=True)
        except (blocks.Fail, ValueError) as exc:
            step['error'] = str(exc)
        except Exception as exc:  # блок не должен ронять движок
            log.exception('автоматизация: действие %s', a['type'])
            step.update(error=f'Внутренняя ошибка: {type(exc).__name__}', retry=True)
        steps.append(step)
    return steps


def outcome(steps: list[dict], attempts: int) -> tuple[str, datetime | None]:
    if all(s['ok'] for s in steps):
        return 'ok', None
    if any(s.get('retry') for s in steps) and attempts < MAX_ATTEMPTS:
        return 'retry', datetime.now(timezone.utc) + RETRY_AFTER[attempts - 1]
    return 'failed', None


async def count_run(pool, client_id: int) -> bool:
    """Учёт запуска под тариф. False — лимит месяца исчерпан."""
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    limit = await pool.fetchval('SELECT max_runs_month FROM automation_limits WHERE client_id=$1', client_id)
    used = await pool.fetchval(
        """INSERT INTO automation_usage(client_id, month, runs) VALUES($1, $2, 1)
           ON CONFLICT(client_id, month) DO UPDATE SET runs = automation_usage.runs + 1
           WHERE $3::int IS NULL OR automation_usage.runs < $3 RETURNING runs""", client_id, month, limit)
    return used is not None


async def too_often(pool, auto) -> bool:
    n = await pool.fetchval('SELECT count(*) FROM automation_runs WHERE automation_id=$1 AND NOT test AND created_at > $2',
                            auto['id'], datetime.now(timezone.utc) - RATE_WINDOW)
    if n < RATE_MAX:
        return False
    await pool.execute(
        "UPDATE automations SET enabled=false, paused_reason=$2, updated_at=now() WHERE id=$1", auto['id'],
        f'Выключен сам: больше {RATE_MAX} запусков за минуту. Похоже на зацикливание.')
    log.warning('автоматизация %s выключена: слишком часто', auto['id'])
    return True


async def fire(pool, auto, event_id: int | None, kind: str, payload: dict):
    spec = json.loads(auto['spec']) if isinstance(auto['spec'], str) else auto['spec']
    if not matches(spec['if'], payload):
        return
    if await too_often(pool, auto):
        return
    payload = plain(payload)
    if not await count_run(pool, auto['client_id']):
        await pool.execute(
            """INSERT INTO automation_runs(automation_id, client_id, event_id, status, payload, steps)
               VALUES($1,$2,$3,'limit',$4,'[]')""", auto['id'], auto['client_id'], event_id, json.dumps(payload, ensure_ascii=False))
        return
    steps = await run_actions(pool, auto['client_id'], spec, payload, kind)
    status, retry_at = outcome(steps, 1)
    await pool.execute(
        """INSERT INTO automation_runs(automation_id, client_id, event_id, status, payload, steps, retry_at)
           VALUES($1,$2,$3,$4,$5,$6,$7)""", auto['id'], auto['client_id'], event_id, status,
        json.dumps(payload, ensure_ascii=False), json.dumps(steps, ensure_ascii=False), retry_at)


async def handle(pool, ev) -> None:
    if ev['automation_id']:
        autos = await pool.fetch('SELECT * FROM automations WHERE id=$1 AND client_id=$2 AND enabled',
                                 ev['automation_id'], ev['client_id'])
    else:
        autos = await pool.fetch("SELECT * FROM automations WHERE client_id=$1 AND enabled AND spec->'trigger'->>'type'=$2",
                                 ev['client_id'], ev['kind'])
    if not autos:
        return
    payload = json.loads(ev['payload']) if ev['payload'] else await build_payload(pool, ev['client_id'], ev['kind'], ev['ref_id'])
    if payload is None:
        return
    for auto in autos:
        await fire(pool, auto, ev['id'], ev['kind'], payload)


async def take_events(pool) -> list:
    return await pool.fetch(
        """UPDATE automation_events SET processed_at=now() WHERE id IN (
             SELECT id FROM automation_events WHERE processed_at IS NULL ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED)
           RETURNING *""", BATCH)


async def retry_due(pool) -> None:
    rows = await pool.fetch(
        """UPDATE automation_runs SET retry_at=NULL WHERE id IN (
             SELECT id FROM automation_runs WHERE status='retry' AND retry_at <= now() ORDER BY retry_at LIMIT $1
             FOR UPDATE SKIP LOCKED) RETURNING *""", BATCH)
    for run in rows:
        auto = await pool.fetchrow('SELECT * FROM automations WHERE id=$1', run['automation_id'])
        if auto is None or not auto['enabled']:
            await pool.execute("UPDATE automation_runs SET status='failed' WHERE id=$1", run['id'])
            continue
        spec = json.loads(auto['spec']) if isinstance(auto['spec'], str) else auto['spec']
        old = json.loads(run['steps'])
        failed = {s['i'] for s in old if not s['ok']}
        kind = spec['trigger']['type']
        new = await run_actions(pool, run['client_id'], spec, json.loads(run['payload'] or '{}'), kind, only=failed)
        by_i = {s['i']: s for s in old} | {s['i']: s for s in new}
        steps = [by_i[i] for i in sorted(by_i)]
        attempts = run['attempts'] + 1
        status, retry_at = outcome(steps, attempts)
        await pool.execute('UPDATE automation_runs SET status=$2, steps=$3, attempts=$4, retry_at=$5 WHERE id=$1',
                           run['id'], status, json.dumps(steps, ensure_ascii=False), attempts, retry_at)


async def schedule(pool, now: datetime | None = None) -> None:
    """Сценарии «каждый день в ЧЧ:ММ» по поясу компании: одно событие в день."""
    now = now or datetime.now(timezone.utc)
    rows = await pool.fetch(
        """SELECT a.id, a.client_id, a.spec, a.last_fired, coalesce(c.timezone, 'Europe/Moscow') tz
           FROM automations a JOIN clients c ON c.id=a.client_id
           WHERE a.enabled AND a.spec->'trigger'->>'type'='schedule.daily'""")
    for r in rows:
        tz = ZoneInfo(r['tz'])
        local = now.astimezone(tz)
        spec = json.loads(r['spec']) if isinstance(r['spec'], str) else r['spec']
        if local.strftime('%H:%M') < spec['trigger']['time'] or r['last_fired'] == local.date().isoformat():
            continue
        day = local.date().isoformat()
        if await pool.fetchval(
                """UPDATE automations SET last_fired=$2 WHERE id=$1 AND last_fired IS DISTINCT FROM $2 RETURNING id""",
                r['id'], day):
            payload = {'date': day, 'day': await day_stats(pool, r['client_id'], tz, local.date())}
            await pool.execute(
                """INSERT INTO automation_events(client_id, kind, automation_id, payload) VALUES($1,'schedule.daily',$2,$3)""",
                r['client_id'], r['id'], json.dumps(plain(payload), ensure_ascii=False))


async def tick(pool) -> None:
    await schedule(pool)
    for ev in await take_events(pool):
        try:
            await handle(pool, ev)
        except Exception:
            log.exception('автоматизация: событие %s', ev['id'])
    await retry_due(pool)


async def loop(pool) -> None:
    while True:
        try:
            await tick(pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('автоматизации: цикл')
        await asyncio.sleep(POLL_SEC)


# --- Пробный запуск и входящий вебхук -------------------------------------------------------------

async def sample_payload(pool, client_id: int, kind: str) -> tuple[dict, bool]:
    """Данные для пробы: последнее настоящее событие этого вида, иначе пример. (payload, настоящее ли)."""
    table = {'lead.added': ('crm_leads', 'id'), 'call.outcome': ('crm_outcomes', 'id'), 'call.reviewed': ('call_reviews', 'call_id')}
    ref = None
    if kind.startswith('deal.'):
        ref = await pool.fetchval('SELECT max(id) FROM deal_events WHERE client_id=$1 AND kind=$2', client_id, kind[5:])
    elif kind in table:
        name, col = table[kind]
        ref = await pool.fetchval(f'SELECT {col} FROM {name} WHERE client_id=$1 ORDER BY created_at DESC LIMIT 1', client_id)
    elif kind == 'schedule.daily':
        tz = ZoneInfo(await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', client_id) or 'Europe/Moscow')
        today = datetime.now(tz).date()
        return plain({'date': today.isoformat(), 'day': await day_stats(pool, client_id, tz, today)}), True
    if ref is not None:
        payload = await build_payload(pool, client_id, kind, ref)
        if payload is not None:
            return plain(payload), True
    return TRIGGERS[kind]['sample'], False


async def test_run(pool, client_id: int, automation_id: int) -> dict:
    """Пробный запуск по-настоящему (сообщение реально уходит), без условий и без учёта в тарифе."""
    auto = await pool.fetchrow('SELECT * FROM automations WHERE id=$1 AND client_id=$2', automation_id, client_id)
    if auto is None:
        raise ValueError('Такого сценария нет.')
    spec = json.loads(auto['spec']) if isinstance(auto['spec'], str) else auto['spec']
    kind = spec['trigger']['type']
    payload, real = await sample_payload(pool, client_id, kind)
    steps = await run_actions(pool, client_id, spec, payload, kind)
    status = 'ok' if all(s['ok'] for s in steps) else 'failed'
    await pool.execute(
        """INSERT INTO automation_runs(automation_id, client_id, test, status, payload, steps) VALUES($1,$2,true,$3,$4,$5)""",
        automation_id, client_id, status, json.dumps(payload, ensure_ascii=False), json.dumps(steps, ensure_ascii=False))
    return {'status': status, 'real_data': real, 'conditions_pass': matches(spec['if'], payload),
            'payload': payload, 'steps': steps}


async def incoming(pool, token: str, raw: bytes) -> bool:
    """Вебхук от другой системы. False — такого включённого сценария нет."""
    if len(raw) > MAX_HOOK_BYTES:
        raise ValueError('Слишком большой запрос.')
    auto = await pool.fetchrow("SELECT id, client_id FROM automations WHERE hook_token=$1 AND enabled", token)
    if auto is None:
        return False
    try:
        data = json.loads(raw or b'{}')
    except ValueError:
        data = {'text': raw.decode('utf-8', 'replace')}
    if not isinstance(data, dict):
        data = {'items': data}
    await pool.execute(
        "INSERT INTO automation_events(client_id, kind, automation_id, payload) VALUES($1,'webhook.in',$2,$3)",
        auto['client_id'], auto['id'], json.dumps({'data': data}, ensure_ascii=False))
    return True
