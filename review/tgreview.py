"""Переписка рабочего Telegram в сделках: чей это клиент и на чём он остыл.

Сообщения складывают tgbusiness.py (бот Telegram Business) и tgchats.py (Telethon), здесь
только чтение базы и модель, в Telegram ничего не уходит. Из цикла astra_worker:

1. привязка чата к клиенту. Номер берётся из контакта, который клиент прислал о себе
   (это делает tgbusiness.save), или из сообщения клиента, если с этим номером уже был
   звонок или он есть в базе номеров. Другие чаты ждут кнопки «Привязать к клиенту» в
   «Сделках»: угадывать по одному номеру из текста нельзя (ИНН, номер коллеги);
2. разбор чата моделью (tg_chat_prompt.md), когда переписка затихла на QUIET_HOURS: клиент
   ли это, этап, температура, сообщение, после которого клиент остыл, и что написать.
   Цитаты код сверяет с текстом сообщений, как у звонков и презентаций.

Отключается TG_REVIEW=off.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import engine

log = logging.getLogger(__name__)

PROMPT_VERSION = 'tg_chat_review_v1'
PROMPT = (Path(__file__).with_name('tg_chat_prompt.md').read_text(encoding='utf-8')
          .split('\n---\n', 1)[-1].strip())
MAX_TOKENS = int(os.getenv('TG_REVIEW_MAX_TOKENS', '6000'))
QUIET_HOURS = float(os.getenv('TG_REVIEW_QUIET_HOURS', '3'))  # столько молчания, и переписку можно разбирать
PER_RUN = int(os.getenv('TG_REVIEW_PER_RUN', '8'))
SYNC_MINUTES = int(os.getenv('TG_REVIEW_SYNC_MINUTES', '10'))
MAX_MESSAGES = 200  # последние сообщения чата в разборе
MAX_CHARS = 30000
NOT_CLIENT_RECHECK = timedelta(days=3)  # «не клиент» проверяем заново не чаще
MAX_FAILS = 3

STAGES = ('paid', 'contract', 'decision_date', 'materials', 'dmp_meeting', 'none')
TEMPERATURES = {'hot', 'warm', 'cold', 'lost'}
CATEGORIES = {'no_discovery', 'generic_pitch', 'price_no_value', 'objection_unhandled', 'no_trust', 'no_dmp',
              'no_next_step', 'pushy', 'unanswered_question', 'slow_reply', 'no_followup', 'materials_no_talk', 'other'}
CONTRACT_FIELDS = ('legal_name', 'inn', 'signer', 'service', 'package', 'competitors', 'region', 'start', 'delivery')
MEDIA_LABELS = {'voice': 'голосовое сообщение, не расшифровано', 'video_note': 'видеокружок, не расшифрован',
                'audio': 'аудио', 'video': 'видео', 'animation': 'гифка', 'photo': 'фото', 'sticker': 'стикер',
                'document': 'файл', 'contact': 'контакт', 'location': 'геопозиция'}
# Российский номер: +7/8 и десять цифр или мобильный без кода. Десять цифр с другого начала — скорее ИНН.
PHONE = re.compile(r'(?<![\d+])(\+?[78][\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}'
                   r'|9\d{2}[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2})(?!\d)')

_last_run: datetime | None = None
_fails: dict[tuple, int] = {}


def enabled() -> bool:
    return os.getenv('TG_REVIEW', 'on').strip().lower() not in ('off', '0', 'no')


def phones_in(texts) -> list[str]:
    """Номера из сообщений, по порядку и без повторов: «+7 (999) 123-45-67» → 79991234567."""
    from .presentations import phone_digits
    out = []
    for text in texts or []:
        for m in PHONE.finditer(text or ''):
            digits = phone_digits(m[1])
            if digits and digits not in out:
                out.append(digits)
    return out


async def link_chats(pool, client_id: int) -> int:
    """Чаты без номера: номер из сообщения клиента, если этот человек уже есть в звонках или в базе."""
    rows = await pool.fetch(
        r"""SELECT c.peer_id, array_agg(m.text ORDER BY m.sent_at) AS texts
            FROM tg_chats c JOIN tg_messages m ON m.client_id=c.client_id AND m.peer_id=c.peer_id
            WHERE c.client_id=$1 AND c.phone IS NULL AND c.kind='user' AND NOT m.outgoing AND m.text ~ '\d{3}'
              AND c.last_message_at > now() - interval '60 days'
            GROUP BY c.peer_id""", client_id)
    has_leads = bool(await pool.fetchval("SELECT to_regclass('crm_leads') IS NOT NULL"))
    linked = 0
    for r in rows:
        found = phones_in(r['texts'])
        if not found:
            continue
        tails = [p[-10:] for p in found]
        known = {k for k, in await pool.fetch(
            r"""SELECT DISTINCT right(regexp_replace(client_number, '\D', '', 'g'), 10) FROM calls
                WHERE client_id=$1 AND right(regexp_replace(client_number, '\D', '', 'g'), 10) = ANY($2::text[])""",
            client_id, tails)}
        if has_leads:
            known |= {k for k, in await pool.fetch(
                r"""SELECT DISTINCT right(regexp_replace(number, '\D', '', 'g'), 10) FROM crm_leads
                    WHERE client_id=$1 AND right(regexp_replace(number, '\D', '', 'g'), 10) = ANY($2::text[])""",
                client_id, tails)}
        if len(known) != 1:
            continue  # ни одного знакомого номера или несколько разных: решит человек кнопкой
        phone = next(p for p in found if p[-10:] in known)
        await pool.execute(
            """UPDATE tg_chats SET phone=$3, updated_at=now(),
                 analysis=coalesce(analysis, '{}'::jsonb) || jsonb_build_object('link', jsonb_build_object('by', 'text'))
               WHERE client_id=$1 AND peer_id=$2 AND phone IS NULL""", client_id, r['peer_id'], phone)
        linked += 1
    return linked


def line(m, tz) -> str:
    who = 'Менеджер' if m['outgoing'] else 'Клиент'
    parts = [f"[{MEDIA_LABELS.get(m['media'], m['media'])}]"] if m['media'] else []
    if m['text']:
        parts.append(m['text'].strip())
    return f"[{m['sent_at'].astimezone(tz):%d.%m %H:%M} {who}] " + ' '.join(parts)


def build_input(chat, messages: list, tz, now: datetime, manager: str | None, history: list[str], skipped: int) -> str:
    name = chat['title'] or 'без имени'
    head = [f"Клиент в Telegram: {name}" + (f" (@{chat['username']})" if chat['username'] else ''),
            f"Менеджер: {manager or 'рабочий аккаунт, кто ведёт клиента, не известно'}",
            f"Сейчас: {now.astimezone(tz):%d.%m.%Y %H:%M}"]
    if messages:
        last = messages[-1]
        hours = (now - last['sent_at']).total_seconds() / 3600
        head.append(f"Последнее сообщение: от {'менеджера' if last['outgoing'] else 'клиента'}, "
                    f"{last['sent_at'].astimezone(tz):%d.%m %H:%M} ({hours:.0f} ч назад)")
    if history:
        head.append('Что было с этим клиентом раньше (сначала новое):\n' + '\n'.join(history))
    lines = [line(m, tz) for m in messages]
    while len(lines) > 1 and sum(len(x) + 1 for x in lines) > MAX_CHARS:
        lines.pop(0)
        skipped += 1
    body = (f'(раньше было ещё {skipped} сообщений, они не показаны)\n' if skipped else '') + '\n'.join(lines)
    return '\n'.join(head) + '\n\nПЕРЕПИСКА:\n' + body


def quote_ok(item: dict, messages: list) -> bool:
    """Цитата дословно есть в сообщении той стороны, которую назвала модель."""
    text = engine.norm(item.get('quote'))
    if len(text.split()) < 2:
        return False
    side = {'client': False, 'manager': True}.get(item.get('from'))
    return any(text in engine.norm(m['text']) for m in messages if m['text'] and (side is None or m['outgoing'] == side))


def strip_quote(item: dict) -> dict:
    return {**item, 'quote': None, 'at': None}


def validate(data: dict, messages: list) -> dict:
    """Согласует ответ модели с перепиской: этап засчитывается только с проверенным сообщением клиента."""
    data = dict(data or {})
    if data.get('kind') != 'client':
        return {'kind': 'not_client', 'reviewed': False, 'rop_line': data.get('rop_line')}
    evidence = [e for e in data.get('stage_evidence') or [] if isinstance(e, dict) and e.get('from') == 'client'
                and quote_ok(e, messages)]
    stage = data.get('stage_after') if data.get('stage_after') in STAGES else 'none'
    if stage != 'none' and not evidence:
        stage = 'none'
    temperature = data.get('temperature') if data.get('temperature') in TEMPERATURES else 'warm'
    moment = data.get('key_moment') if isinstance(data.get('key_moment'), dict) else None
    if moment:
        if moment.get('category') not in CATEGORIES:
            moment['category'] = 'other'
        if moment.get('quote') and not quote_ok(moment, messages):
            moment = strip_quote(moment)
    if stage != 'none' and temperature == 'hot':
        moment = None
    contract = data.get('contract') if isinstance(data.get('contract'), dict) else {}
    contract['missing'] = [] if temperature == 'lost' else [f for f in contract.get('missing') or [] if f in CONTRACT_FIELDS]
    data.update(kind='client', stage_after=stage, stage_evidence=evidence, moved=stage != 'none', temperature=temperature,
                key_moment=moment, contract=contract, reviewed=True,
                positives=[p for p in data.get('positives') or [] if isinstance(p, dict)
                           and (not p.get('quote') or quote_ok(p, messages))][:2])
    return data


async def review_chat(pool, chat, tz, now: datetime, call=None) -> float:
    """Один чат: собрать переписку и что известно о клиенте, спросить модель, сохранить разбор."""
    if call is None:
        from analysis import _call as call
    from .presentations import previous
    rows = await pool.fetch(
        """SELECT message_id, sent_at, outgoing, text, media FROM tg_messages
           WHERE client_id=$1 AND peer_id=$2 ORDER BY sent_at DESC, message_id DESC LIMIT $3""",
        chat['client_id'], chat['peer_id'], MAX_MESSAGES + 1)
    skipped = await pool.fetchval('SELECT count(*) FROM tg_messages WHERE client_id=$1 AND peer_id=$2',
                                  chat['client_id'], chat['peer_id']) - min(len(rows), MAX_MESSAGES)
    messages = list(reversed(rows[:MAX_MESSAGES]))
    manager = None
    if chat['phone'] or chat['extension']:
        manager = await pool.fetchval(
            r"""SELECT e.full_name FROM employees e WHERE e.client_id=$1 AND e.role='manager' AND e.extension = coalesce($2,
                  (SELECT c.extension FROM calls c WHERE c.client_id=$1
                     AND right(regexp_replace(c.client_number, '\D', '', 'g'), 10) = right($3, 10)
                   ORDER BY c.call_started_at DESC LIMIT 1)) LIMIT 1""",
            chat['client_id'], chat['extension'], chat['phone'] or '')
    history = await previous(pool, chat['client_id'], chat['phone'])
    raw, cost = await call(PROMPT, build_input(chat, messages, tz, now, manager, history, skipped), MAX_TOKENS)
    data = validate(raw, messages)
    data.update(prompt_version=PROMPT_VERSION, messages=len(messages), cost=cost)
    # Отметку о привязке (кто и как связал чат с клиентом) разбор не стирает.
    await pool.execute(
        """UPDATE tg_chats SET analysis=$3::jsonb || jsonb_strip_nulls(jsonb_build_object('link', analysis->'link')),
             analyzed_at=$4, updated_at=now() WHERE client_id=$1 AND peer_id=$2""",
        chat['client_id'], chat['peer_id'], json.dumps(data, ensure_ascii=False), now)
    if cost:
        from .prompts import add_spend
        await add_spend(pool, chat['client_id'], cost)
    return cost


async def process(pool, client_id: int, now: datetime, limit: int = PER_RUN, call=None) -> int:
    """Затихшие чаты с новыми сообщениями после прошлого разбора."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', client_id) or 'Europe/Moscow')
    chats = await pool.fetch(
        """SELECT c.* FROM tg_chats c
           WHERE c.client_id=$1 AND c.kind='user' AND c.last_message_at < $2
             AND (c.analyzed_at IS NULL OR c.analyzed_at < c.last_message_at)
             AND NOT (coalesce(c.analysis->>'kind', '') = 'not_client' AND coalesce(c.analyzed_at > $3, false))
             AND EXISTS (SELECT 1 FROM tg_messages m WHERE m.client_id=c.client_id AND m.peer_id=c.peer_id
                           AND NOT m.outgoing AND m.text IS NOT NULL)
           ORDER BY c.last_message_at DESC LIMIT $4""",
        client_id, now - timedelta(hours=QUIET_HOURS), now - NOT_CLIENT_RECHECK, limit * 2)
    done = 0
    for chat in chats:
        if done >= limit:
            break
        attempt = (client_id, chat['peer_id'], chat['last_message_id'])
        if _fails.get(attempt, 0) >= MAX_FAILS:
            continue
        try:
            await review_chat(pool, chat, tz, now, call)
            done += 1
        except Exception:
            _fails[attempt] = _fails.get(attempt, 0) + 1
            log.exception('переписка %s не разобрана', chat['peer_id'])
    return done


async def maybe_run(pool, now: datetime | None = None, call=None) -> dict | None:
    """Из цикла astra_worker: раз в SYNC_MINUTES привязать новые чаты и разобрать затихшие."""
    global _last_run
    if not enabled():
        return None
    now = now or datetime.now(timezone.utc)
    if _last_run and now - _last_run < timedelta(minutes=SYNC_MINUTES):
        return None
    _last_run = now
    if not await pool.fetchval("SELECT to_regclass('tg_chats') IS NOT NULL"):
        return None
    client_id = await pool.fetchval('SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1')
    if client_id is None:
        return None
    stats = {'linked': await link_chats(pool, client_id), 'reviewed': await process(pool, client_id, now, call=call)}
    if stats['linked'] or stats['reviewed']:
        log.info('Telegram: привязано к клиентам %s, разобрано переписок %s', stats['linked'], stats['reviewed'])
    return stats


if __name__ == '__main__':
    # python -m review.tgreview status — сколько переписок привязано к клиентам и разобрано.
    import asyncio
    import sys

    async def _status():
        import asyncpg
        from scripts.kitchen_backfill import load_env
        load_env(Path(__file__).resolve().parents[1] / '.env')
        pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=1)
        try:
            row = await pool.fetchrow(
                """SELECT count(*) AS chats, count(*) FILTER (WHERE phone IS NOT NULL) AS linked,
                          count(*) FILTER (WHERE analysis->>'reviewed' = 'true') AS reviewed,
                          count(*) FILTER (WHERE analysis->>'kind' = 'not_client') AS not_client,
                          max(analyzed_at) AS last
                     FROM tg_chats WHERE kind='user'""")
            print(f"чатов {row['chats']}, с номером клиента {row['linked']}, разобрано {row['reviewed']}, "
                  f"не клиенты {row['not_client']}"
                  + (f", последний разбор {row['last']:%d.%m %H:%M} UTC" if row['last'] else ', разборов пока нет')
                  + ('' if enabled() else ' (разбор выключен: TG_REVIEW=off)'))
        finally:
            await pool.close()

    if sys.argv[1:2] == ['status']:
        asyncio.run(_status())
