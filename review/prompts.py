"""Режим промта: версии промтов, личные и общие, и откат к любому сохранению.

Править могут люди из PROMPT_EDITORS (Telegram ID через запятую). Что правится:

- `review`: разбор звонка. Редактируется всё до раздела «Формат ответа»; формат
  JSON остаётся из кода, иначе бот не сможет разложить ответ по сообщениям.
  Общая версия разбирает все звонки. Личная версия меняет только полную копию
  разбора, которая приходит этому человеку (REVIEW_COPY_TO): на каждый звонок
  это ещё один запрос к модели.
- `morning`, `weekly`, `monthly`: вопрос, который бот задаёт агенту РОПа для
  утренней планёрки, сводки за неделю и отчёта за месяц. Даты подставляются
  вместо {start}, {end} (и {prev_start}, {prev_end} для недели).

Нет общей строки в prompt_active — действует исходный текст из кода. Нет личной —
человек получает общую версию. Исходные тексты сохраняются первой версией
(seed), чтобы к ним всегда можно было вернуться, даже если код потом изменится.

Если личный или общий промт сломался (ошибка модели или ответ не по формату),
человек получает сообщение с причиной и стандартную версию.
"""
from __future__ import annotations

import difflib
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import engine

log = logging.getLogger(__name__)

FORMAT_MARK = '\n## Формат ответа'
REVIEW_HEAD, _rest = engine.PROMPT.split(FORMAT_MARK, 1)
REVIEW_TAIL = FORMAT_MARK + _rest
ORIGINAL_NOTE = 'Исходная (до режима промта)'
DRAFT_TTL = timedelta(hours=3)
MAX_BODY = 20000
KNOWN_KINDS = engine.REVIEWED | {'no_conversation', 'not_sales', 'other_product'}

DIGESTS = {
    'morning': (
        "Сформируй три темы для утренней планёрки на основе вчерашних звонков "
        "(период {start}–{end}). Используй get_stats и "
        "get_criteria_breakdown по всему отделу (manager_extension не указывай). "
        "Если хочешь сказать что-то персонально про менеджера — сначала как обычно "
        "проверь через verify_conclusion. Три коротких пункта, без вступлений, сразу по делу."
    ),
    'weekly': (
        "Сформируй сводку за прошедшую неделю ({start}–{end}) с "
        "динамикой относительно недели до этого ({prev_start}–{prev_end}). "
        "Используй compare_periods по всему отделу и get_criteria_breakdown за последнюю неделю. "
        "Персональные выводы о менеджере — только через verify_conclusion. Кратко, по-деловому."
    ),
    'monthly': (
        "Составь отчёт о прошедшем месяце ({start}–{end}) языком выручки и "
        "встреч, а не критериев оценки звонков — не используй названия критериев вроде "
        "«извлекающие вопросы», это не для читателя-нетехнического. Используй get_stats по всему "
        "отделу. Если есть признак проблемы с качеством лидов — используй "
        "get_lead_diagnosis_signal, но обязательно назови reviewed_calls и предупреди о смещённой "
        "выборке, если она маленькая. 5-8 предложений, по-деловому."
    ),
}

TITLES = {
    'review': 'Разбор звонка',
    'morning': 'Планёрка 9:00',
    'weekly': 'Сводка за неделю',
    'monthly': 'Отчёт за месяц',
}
KINDS = tuple(TITLES)


def editors() -> list[int]:
    return [int(t) for t in re.findall(r'-?\d+', os.getenv('PROMPT_EDITORS', ''))]


def is_editor(tg_id) -> bool:
    return tg_id in editors()


def default(kind: str) -> str:
    if kind == 'review':
        return REVIEW_HEAD.strip()
    return DIGESTS[kind]


def full_review(head: str) -> str:
    """Редактируемая часть плюс формат ответа из кода."""
    return head.rstrip() + '\n' + REVIEW_TAIL


# ---------------------------------------------------------------- периоды

def period_yesterday(tz_name: str, today=None) -> dict:
    today = today or datetime.now(ZoneInfo(tz_name)).date()
    return {'start': (today - timedelta(days=1)).isoformat(), 'end': today.isoformat()}


def period_week(tz_name: str, weeks_ago: int, today=None) -> dict:
    today = today or datetime.now(ZoneInfo(tz_name)).date()
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks_ago)
    return {'start': start.isoformat(), 'end': (start + timedelta(days=7)).isoformat()}


def period_prev_month(tz_name: str, today=None) -> dict:
    today = today or datetime.now(ZoneInfo(tz_name)).date()
    first = today.replace(day=1)
    return {'start': (first - timedelta(days=1)).replace(day=1).isoformat(), 'end': first.isoformat()}


def digest_values(kind: str, tz_name: str, today=None) -> dict:
    if kind == 'morning':
        return period_yesterday(tz_name, today)
    if kind == 'weekly':
        last, before = period_week(tz_name, 1, today), period_week(tz_name, 2, today)
        return {**last, 'prev_start': before['start'], 'prev_end': before['end']}
    if kind == 'monthly':
        return period_prev_month(tz_name, today)
    raise ValueError(kind)


def render_digest(template: str, values: dict) -> str:
    """Даты вместо {start}/{end}. Если правка их убрала, период дописывается в конец,
    чтобы агент не гадал, за какие дни отчёт."""
    text = template
    for key, value in values.items():
        text = text.replace('{' + key + '}', str(value))
    if not any('{' + key + '}' in template for key in values):
        period = f"Период: {values['start']}–{values['end']}"
        if 'prev_start' in values:
            period += f"; для сравнения {values['prev_start']}–{values['prev_end']}"
        text = text.rstrip() + '\n\n' + period + '.'
    return text


# ---------------------------------------------------------------- хранение

async def company_of(pool, company: int | None) -> int | None:
    """Чьи промты: компания (clients.id). Не указана — наша (tools.home_client_id), как до многоклиентности.
    Общая версия у каждой компании своя; у новой компании действует исходный текст из кода."""
    if company is not None:
        return company
    from tools import home_client_id
    return await home_client_id(pool)


async def seed(pool, company: int | None = None) -> int:
    """Исходные тексты первой общей версией каждого вида: точка отката «как было»."""
    company = await company_of(pool, company)
    added = 0
    for kind in KINDS:
        exists = await pool.fetchval(
            'SELECT 1 FROM prompt_versions WHERE client_id=$1 AND kind=$2 AND owner_tg=0 AND note=$3', company, kind, ORIGINAL_NOTE)
        if not exists:
            await pool.execute('INSERT INTO prompt_versions(client_id, kind, owner_tg, body, note) VALUES($1, $2, 0, $3, $4)',
                               company, kind, default(kind), ORIGINAL_NOTE)
            added += 1
    return added


async def original_id(pool, kind: str, company: int | None = None) -> int:
    company = await company_of(pool, company)
    await seed(pool, company)
    return await pool.fetchval(
        'SELECT id FROM prompt_versions WHERE client_id=$1 AND kind=$2 AND owner_tg=0 AND note=$3 ORDER BY id LIMIT 1',
        company, kind, ORIGINAL_NOTE)


async def active(pool, kind: str, owner: int, company: int | None = None):
    """Действующее сохранение. До миграции 012 таблиц нет: тогда всё как раньше."""
    try:
        return await _active(pool, kind, owner, await company_of(pool, company))
    except Exception as exc:
        if exc.__class__.__name__ == 'UndefinedTableError':
            return None
        raise


async def _active(pool, kind: str, owner: int, company: int):
    return await pool.fetchrow(
        """SELECT v.id, v.body, v.note, v.created_at, v.created_by, a.updated_by, a.updated_at
           FROM prompt_active a JOIN prompt_versions v ON v.id=a.version_id
           WHERE a.client_id=$1 AND a.kind=$2 AND a.owner_tg=$3""", company, kind, owner)


async def common(pool, kind: str, company: int | None = None) -> tuple[str, int | None]:
    """Текст для всех в компании и номер версии (None — исходный из кода)."""
    row = await active(pool, kind, 0, company)
    return (row['body'], row['id']) if row else (default(kind), None)


async def resolve(pool, kind: str, tg_id: int, company: int | None = None) -> tuple[str, str, int | None]:
    """Что действует для человека: ('personal' | 'common' | 'default', номер версии)."""
    row = await active(pool, kind, tg_id, company) if tg_id else None
    if row:
        return row['body'], 'personal', row['id']
    body, version = await common(pool, kind, company)
    return body, 'common' if version else 'default', version


async def _set_active(conn, company: int, kind: str, owner: int, version_id: int, by: int) -> None:
    await conn.execute(
        """INSERT INTO prompt_active(client_id, kind, owner_tg, version_id, updated_by) VALUES($1,$2,$3,$4,$5)
           ON CONFLICT(client_id, kind, owner_tg) DO UPDATE SET version_id=EXCLUDED.version_id,
             updated_by=EXCLUDED.updated_by, updated_at=now()""", company, kind, owner, version_id, by)


async def save(pool, kind: str, body: str, note: str | None, by: int, scope: str, company: int | None = None) -> int:
    """scope 'me' — личная версия; 'all' — общая для компании (свою личную при этом снимает,
    чтобы сохранивший видел то же, что все)."""
    if kind not in KINDS or scope not in {'me', 'all'}:
        raise ValueError('неизвестный вид или область')
    body = (body or '').strip()
    if not body or len(body) > MAX_BODY:
        raise ValueError('Пустой или слишком длинный текст промта.')
    company = await company_of(pool, company)
    await seed(pool, company)
    owner = by if scope == 'me' else 0
    async with pool.acquire() as conn, conn.transaction():
        version = await conn.fetchval(
            'INSERT INTO prompt_versions(client_id, kind, owner_tg, body, note, created_by) VALUES($1,$2,$3,$4,$5,$6) RETURNING id',
            company, kind, owner, body, (note or '')[:300] or None, by)
        await _set_active(conn, company, kind, owner, version, by)
        if scope == 'all':
            await conn.execute('DELETE FROM prompt_active WHERE client_id=$1 AND kind=$2 AND owner_tg=$3', company, kind, by)
    return version


async def activate(pool, kind: str, version_id: int, by: int, scope: str, company: int | None = None) -> dict:
    """Откат к сохранению: своему, общему или исходному."""
    company = await company_of(pool, company)
    row = await pool.fetchrow('SELECT * FROM prompt_versions WHERE id=$1 AND kind=$2 AND client_id=$3', version_id, kind, company)
    if row is None or row['owner_tg'] not in (0, by) or scope not in {'me', 'all'}:
        raise ValueError('Сохранение не найдено.')
    async with pool.acquire() as conn, conn.transaction():
        await _set_active(conn, company, kind, by if scope == 'me' else 0, version_id, by)
        if scope == 'all':
            await conn.execute('DELETE FROM prompt_active WHERE client_id=$1 AND kind=$2 AND owner_tg=$3', company, kind, by)
    return dict(row)


async def drop_personal(pool, kind: str, by: int, company: int | None = None) -> bool:
    """Вернуться к общей версии."""
    company = await company_of(pool, company)
    return (await pool.execute('DELETE FROM prompt_active WHERE client_id=$1 AND kind=$2 AND owner_tg=$3',
                               company, kind, by)) != 'DELETE 0'


async def versions(pool, kind: str, tg_id: int, limit: int = 10, company: int | None = None) -> list[dict]:
    """Последние сохранения (общие и свои) плюс исходная, даже если она старше."""
    company = await company_of(pool, company)
    first = await original_id(pool, kind, company)
    rows = await pool.fetch(
        """SELECT id, owner_tg, note, created_by, created_at FROM prompt_versions
           WHERE client_id=$1 AND kind=$2 AND owner_tg IN (0, $3) ORDER BY id DESC LIMIT $4""", company, kind, tg_id, limit)
    out = [dict(r) for r in rows]
    if first not in {r['id'] for r in out}:
        out.append(dict(await pool.fetchrow(
            'SELECT id, owner_tg, note, created_by, created_at FROM prompt_versions WHERE id=$1', first)))
    return out


async def version(pool, version_id: int, company: int | None = None):
    return await pool.fetchrow('SELECT * FROM prompt_versions WHERE id=$1 AND client_id=$2',
                               version_id, await company_of(pool, company))


# ---------------------------------------------------------------- черновик

async def draft(pool, tg_id: int):
    row = await pool.fetchrow('SELECT * FROM prompt_drafts WHERE telegram_user_id=$1 AND updated_at > now() - $2::interval',
                              tg_id, DRAFT_TTL)
    return row


async def open_draft(pool, tg_id: int, kind: str):
    body, _, _ = await resolve(pool, kind, tg_id)
    return await pool.fetchrow(
        """INSERT INTO prompt_drafts(telegram_user_id, kind, body, start_body) VALUES($1,$2,$3,$3)
           ON CONFLICT(telegram_user_id) DO UPDATE SET kind=EXCLUDED.kind, body=EXCLUDED.body,
             start_body=EXCLUDED.start_body, note=NULL, history='[]'::jsonb, preview_ok=NULL,
             previewed_body=NULL, updated_at=now()
           RETURNING *""", tg_id, kind, body)


async def update_draft(pool, tg_id: int, **fields) -> None:
    allowed = {'body', 'start_body', 'note', 'history', 'preview_ok', 'previewed_body'}
    if not fields or set(fields) - allowed:
        raise ValueError(fields)
    keys = sorted(fields)
    sets = ', '.join(f"{k}=${i + 2}{'::jsonb' if k == 'history' else ''}" for i, k in enumerate(keys))
    values = [json.dumps(fields[k], ensure_ascii=False) if k == 'history' else fields[k] for k in keys]
    await pool.execute(f'UPDATE prompt_drafts SET {sets}, updated_at=now() WHERE telegram_user_id=$1', tg_id, *values)


async def close_draft(pool, tg_id: int) -> None:
    await pool.execute('DELETE FROM prompt_drafts WHERE telegram_user_id=$1', tg_id)


# ---------------------------------------------------------------- правка через модель

EDITOR = """Ты помогаешь руководителю отдела продаж компании Averon править промт его бота.
Промт: «{title}». {rules}

Руководитель пишет обычными словами, что хочет поменять. Меняй только то, о чём он
просит; остальной текст оставляй дословно. Если просьба неясна, это вопрос или для неё
не нужно менять текст, ответь в reply и верни "prompt": null. Не выдумывай данных,
которых у бота нет.

Верни только JSON:
{{"reply": "коротко по-русски, на «вы»: что сделал или что уточнить",
  "prompt": "полный новый текст промта или null",
  "changes": "одна строка: что изменено (для списка сохранений) или null"}}"""

RULES = {
    'review': (
        "Это инструкция модели, которая разбирает каждый звонок менеджера. Модель отвечает JSON; "
        "раздел «Формат ответа» с полями JSON меняется только в коде и в этот текст не входит: "
        "не добавляй новых полей и не переименовывай существующие (manager_text, rop_line, details, "
        "key_moment и другие). Руководитель получает полную копию разбора: это текст поля details. "
        "Менеджер получает manager_text, РОП в горячих случаях rop_line. Правила оценки, "
        "результаты и стиль текстов менять можно."
    ),
    'digest': (
        "Это вопрос, который бот по расписанию задаёт аналитику отдела ({when}). Аналитик отвечает по "
        "данным звонков через свои инструменты: get_stats, get_criteria_breakdown, compare_periods, "
        "find_calls, get_call, verify_conclusion, get_lead_diagnosis_signal. Вместо {{start}}, {{end}}, "
        "{{prev_start}}, {{prev_end}} бот подставляет даты периода: эти метки сохраняй. Можно менять, "
        "что включать в отчёт, что убрать, длину и стиль."
    ),
}
WHEN = {'morning': 'каждое утро в 9:00, за вчера', 'weekly': 'по понедельникам, за прошлую неделю',
        'monthly': '1 числа, за прошлый месяц'}


def editor_system(kind: str) -> str:
    rules = RULES['review'] if kind == 'review' else RULES['digest'].format(when=WHEN[kind])
    return EDITOR.format(title=TITLES[kind], rules=rules)


def editor_input(body: str, history: list, ask: str) -> str:
    past = '\n'.join(f"- {h.get('ask')} → {h.get('reply')}" for h in history[-6:]) or 'нет'
    return (f"ТЕКУЩИЙ ТЕКСТ ПРОМТА:\n<<<\n{body}\n>>>\n\nРАНЬШЕ В ЭТОЙ ПРАВКЕ:\n{past}\n\n"
            f"ПРОСЬБА СЕЙЧАС:\n{ask}")


async def edit(kind: str, body: str, history: list, ask: str, call=None) -> tuple[dict, float]:
    """Один шаг правки: ответ модели и, если она поменяла текст, новый текст."""
    if call is None:
        from analysis import _call as call
    data, cost = await call(editor_system(kind), editor_input(body, history, ask), 12000)
    data = data if isinstance(data, dict) else {}
    new = data.get('prompt')
    new = new.strip() if isinstance(new, str) and new.strip() else None
    if new and len(new) > MAX_BODY:
        new = None
        data['reply'] = (data.get('reply') or '') + '\n(Текст вышел слишком длинным, правка не принята.)'
    return {'reply': str(data.get('reply') or '').strip() or 'Готово.',
            'prompt': new if new != body else None,
            'changes': str(data.get('changes') or '').strip() or None}, cost


def diff(old: str, new: str, limit: int = 14) -> str:
    """Что поменялось, строками: считает код, а не модель."""
    lines = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm='', n=0):
        if line.startswith(('---', '+++', '@@')) or not line[1:].strip():
            continue
        mark = '➕' if line[0] == '+' else '➖'
        text = line[1:].strip()
        lines.append(f"{mark} {html.escape(text[:220] + ('…' if len(text) > 220 else ''))}")
    if len(lines) > limit:
        lines = lines[:limit] + [f'… и ещё строк: {len(lines) - limit}']
    return '\n'.join(lines)


# ---------------------------------------------------------------- применение

def broken_review(data: dict, standard: dict | None = None) -> str | None:
    """Причина, по которой ответ модели по личному или общему промту не годится."""
    if not isinstance(data, dict) or data.get('kind') not in KNOWN_KINDS:
        return 'ответ модели не по формату (нет понятного типа звонка)'
    if standard and standard.get('reviewed') and not data.get('reviewed'):
        return 'по этому промту звонок не разобран, хотя стандартный разбор его разобрал'
    return None


def short_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    if isinstance(exc, ValueError) and 'Expecting' in text:
        return 'модель вернула текст не в формате JSON'
    return text[:300]


_last_notice: dict[str, float] = {}


async def notify_editors(bot, text: str, key: str | None = None, every: int = 3600) -> None:
    """Сообщение людям из PROMPT_EDITORS; одинаковые сбои — не чаще раза в час."""
    if key:
        if time.monotonic() - _last_notice.get(key, -every) < every:
            return
        _last_notice[key] = time.monotonic()
    for chat in editors():
        try:
            await bot.send_message(chat, text, parse_mode=None)
        except Exception:
            log.exception('не удалось отправить уведомление о промте chat=%s', chat)


async def run_common_review(pool, transcript: str, meta: dict, bot=None, run=None,
                            company: int | None = None) -> tuple[dict, float]:
    """Разбор для всех: общая версия компании, а если она сломалась — исходная из кода."""
    run = run or engine.run
    body, version_id = await common(pool, 'review', company)
    if version_id is None or body == default('review'):
        return await run(transcript, meta)
    cost = 0.0
    try:
        data, cost = await run(transcript, meta, prompt=full_review(body))
        reason = broken_review(data)
    except Exception as exc:
        log.exception('общий промт разбора #%s не сработал', version_id)
        reason = short_error(exc)
    if reason is None:
        return data, cost
    if bot is not None:
        await notify_editors(bot, f'⚠️ Общий промт разбора (сохранение #{version_id}) не сработал: {reason}. '
                                  'Звонки разбираются исходным промтом. Вернуть другое сохранение: '
                                  '«✏️ Режим промта» → «Разбор звонка» → «Сохранения».',
                             key=f'review:{version_id}')
    data, more = await run(transcript, meta)
    return data, cost + more


async def review_meta(pool, row: dict) -> dict:
    from . import store
    meta = row['meta']
    seconds = int(meta.get('duration_seconds') or 0)
    started = meta.get('call_started_at')
    return {'manager': meta.get('manager') or f"доб. {row['extension']}",
            'date': started.strftime('%d.%m.%Y %H:%M') if started else None,
            'duration': f'{seconds // 60}:{seconds % 60:02d}' if seconds else None,
            'previous': await store.previous(pool, row['client_id'], meta.get('client_number'), row['call_id'])}


async def personal_review(pool, row: dict, chat: int, run=None) -> tuple[str | None, str | None, float]:
    """Копия разбора по личному промту: (текст или None, причина сбоя или None, стоимость).
    None, None — личного промта нет, отправляется стандартная копия."""
    from . import render
    mine = await active(pool, 'review', chat, row['client_id'])
    if mine is None or mine['body'] == (await common(pool, 'review', row['client_id']))[0]:
        return None, None, 0.0  # стандартная копия уже по этому тексту
    run = run or engine.run
    transcript = await pool.fetchval('SELECT transcript FROM calls WHERE id=$1', row['call_id'])
    try:
        data, cost = await run(transcript or '', await review_meta(pool, row), prompt=full_review(mine['body']))
    except Exception as exc:
        log.exception('личный промт разбора #%s не сработал, call id=%s', mine['id'], row['call_id'])
        return None, short_error(exc), 0.0
    reason = broken_review(data, row['data'])
    if reason:
        return None, reason, cost
    return render.details_message(row['meta'], data), None, cost


async def add_spend(pool, client_id: int, units: float) -> None:
    if not units:
        return
    tz = await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', client_id) or 'Europe/Moscow'
    await pool.execute(
        """INSERT INTO astra_daily_spend (client_id, day, spent_units) VALUES ($1, $2, $3)
           ON CONFLICT (client_id, day) DO UPDATE SET spent_units = astra_daily_spend.spent_units + EXCLUDED.spent_units""",
        client_id, datetime.now(ZoneInfo(tz)).date(), units)


if __name__ == '__main__':
    # python -m review.prompts seed — сохранить исходные тексты (точка отката «как было»).
    import asyncio
    import sys

    async def _main():
        import asyncpg
        from scripts.kitchen_backfill import load_env
        from pathlib import Path
        load_env(Path(__file__).resolve().parents[1] / '.env')
        pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=2)
        try:
            print('исходных версий сохранено:', await seed(pool))
            for kind in KINDS:
                body, how, vid = await resolve(pool, kind, 0)
                print(kind, how, vid, len(body))
        finally:
            await pool.close()

    if sys.argv[1:] == ['seed']:
        asyncio.run(_main())
