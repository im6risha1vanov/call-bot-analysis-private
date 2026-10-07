"""Versioned derived cache in the existing expanded column; no migration.

Legacy text is retained, as are all input scores/versions. Delivery receipts
commit before/after each Telegram call, so a failed later part cannot resend
the earlier parts. Unknown outcomes require explicit human inspection.
"""
from __future__ import annotations
import json

from .extended_review import VERSION

MARKER = 'averon.overview.cache.v1'


def decode(raw):
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else None
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict) and parsed.get('_format') == MARKER:
        return parsed
    return {'_format': MARKER, 'entries': {}, 'legacy_text': raw}


def encode(data):
    return json.dumps(data, ensure_ascii=False)


def entry(cache):
    return cache['entries'].get(cache.get('selected_version', VERSION))


def key(actor):
    return str(actor.telegram_user_id)


def rollback_text(raw):
    """Unwrap only our derived cache; never alter an unrelated historical text."""
    parsed = decode(raw)
    if parsed.get('legacy_text') == raw:
        return False, raw
    return True, parsed.get('legacy_text')


async def claim(pool, actor, run_id, count, content_sha):
    from .store import access
    await access(pool, actor, run_id)
    async with pool.acquire() as conn, conn.transaction():
        raw = await conn.fetchval('SELECT expanded FROM call_report_runs WHERE id=$1 FOR UPDATE', run_id)
        cache = decode(raw); item = entry(cache)
        if not item:
            raise ValueError('Обзор ещё не подготовлен.')
        receipt = item.setdefault('delivery', {}).setdefault(key(actor),
                       {'content_sha256': content_sha,
                        'generation': item.get('delivery_generations', {}).get(key(actor), 0),
                        'parts': [{'status': 'pending'} for _ in range(count)]})
        if receipt['content_sha256'] != content_sha or len(receipt['parts']) != count:
            raise ValueError('Формат обзора изменился. Откройте его заново после проверки доставки.')
        for index, part in enumerate(receipt['parts']):
            if part['status'] == 'sent':
                continue
            if part['status'] != 'pending':
                return part['status'], index
            part['status'] = 'sending'
            await conn.execute('UPDATE call_report_runs SET expanded=$2 WHERE id=$1', run_id, encode(cache))
            return 'claimed', index
        return 'complete', count


async def delivered_view(pool, actor, run_id):
    """Only the caller's receipt; a message ID is not an access grant."""
    from .store import access
    row = await access(pool, actor, run_id)
    item = entry(decode(row['expanded'])) or {}
    receipt = item.get('delivery', {}).get(key(actor)) or {}
    parts = receipt.get('parts') or []
    if not parts or any(part['status'] != 'sent' for part in parts):
        return None
    return receipt


async def reopen(pool, actor, run_id, expected_generation):
    """Explicit cached viewing, once per button generation, with no paid job.

    Preserve old receipts and messages. Pending/unknown sends never reset.
    A duplicate click carries the old generation and cannot start another view.
    """
    from .store import access
    if type(expected_generation) is not int or expected_generation < 0:
        raise ValueError('Некорректная кнопка повторного открытия.')
    await access(pool, actor, run_id)
    async with pool.acquire() as conn, conn.transaction():
        raw = await conn.fetchval('SELECT expanded FROM call_report_runs WHERE id=$1 FOR UPDATE', run_id)
        cache = decode(raw); item = entry(cache) or {}
        receipt = item.get('delivery', {}).get(key(actor))
        if not receipt:
            raise ValueError('Эта кнопка устарела. Нажмите «Расширенный обзор» под разбором.')
        generation = receipt.get('generation', 0)
        if generation != expected_generation:
            return False
        parts = receipt.get('parts') or []
        if not parts or any(part['status'] != 'sent' for part in parts):
            raise ValueError('Отправка ещё не завершена или её результат неизвестен. Повторная отправка заблокирована.')
        item.setdefault('delivery_history', {}).setdefault(key(actor), []).append(receipt)
        next_generation = generation + 1
        item.setdefault('delivery_generations', {})[key(actor)] = next_generation
        item['delivery'][key(actor)] = {'generation': next_generation,
            'content_sha256': receipt['content_sha256'],
            'parts': [{'status': 'pending'} for _ in parts]}
        await conn.execute('UPDATE call_report_runs SET expanded=$2 WHERE id=$1', run_id, encode(cache))
        return True


async def record(pool, actor, run_id, index, status, message_id=None):
    # Access was checked before claiming; a receipt is scoped to that same actor.
    async with pool.acquire() as conn, conn.transaction():
        raw = await conn.fetchval('SELECT expanded FROM call_report_runs WHERE id=$1 FOR UPDATE', run_id)
        cache = decode(raw)
        parts = entry(cache)['delivery'][key(actor)]['parts']
        if parts[index]['status'] != 'sending':
            raise ValueError('Состояние доставки изменилось; повторная отправка заблокирована.')
        parts[index].update(status=status, message_id=message_id)
        await conn.execute('UPDATE call_report_runs SET expanded=$2 WHERE id=$1', run_id, encode(cache))


async def close(pool, bot, actor, run_id, clicked_message_id):
    """Never delete an ordinary report or someone else's clicked message."""
    from .store import access
    await access(pool, actor, run_id)
    async with pool.acquire() as conn, conn.transaction():
        raw = await conn.fetchval('SELECT expanded FROM call_report_runs WHERE id=$1 FOR UPDATE', run_id)
        cache = decode(raw); item = entry(cache)
        receipt = (item or {}).get('delivery', {}).get(key(actor)) or {}
        ids = [part.get('message_id') for part in receipt.get('parts', []) if part.get('status') == 'sent']
        if clicked_message_id not in ids:
            raise ValueError('Эта кнопка относится к прежнему обзору. Основной разбор остаётся доступным.')
        # Finish deletion before allowing a deliberate reopen. A failure keeps
        # receipts intact; retrying close does not send the analysis again.
        from aiogram.exceptions import TelegramBadRequest
        for message_id in ids:
            try:
                await bot.delete_message(actor.telegram_user_id, message_id)
            except TelegramBadRequest as exc:
                if 'message to delete not found' not in str(exc).lower():
                    raise
        item.setdefault('delivery_generations', {})[key(actor)] = receipt.get('generation', 0) + 1
        del item['delivery'][key(actor)]
        await conn.execute('UPDATE call_report_runs SET expanded=$2 WHERE id=$1', run_id, encode(cache))
