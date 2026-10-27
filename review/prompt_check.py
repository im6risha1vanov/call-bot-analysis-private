"""Проверка нового промта разбора на эталонных звонках перед «Сохранить для всех».

Пример в режиме промта показывает один последний звонок. По одному звонку не видно,
что правка сломала остальные: например, успешные звонки стали «без результата».
Поэтому перед сохранением для всех бот берёт одни и те же последние разобранные
звонки (примерно поровну с результатом и без), разбирает каждый действующим общим
промтом и новым и показывает, где изменился итог: тип звонка, результат, шанс,
категория потери. Оба текста прогоняются сейчас, на одной модели, поэтому разница
показывает правку, а не то, что звонок когда-то разбирала другая версия.

Модель отвечает не совсем одинаково даже на один и тот же текст, поэтому пара
изменений на 20 звонков бывает и без правки. Смотреть надо на то, что поменялось
и в какую сторону.
"""
from __future__ import annotations

import asyncio
import collections
import html
import logging
import os

from . import engine, prompts

log = logging.getLogger(__name__)

CALLS = int(os.getenv('PROMPT_CHECK_CALLS', '20'))
PARALLEL = int(os.getenv('PROMPT_CHECK_PARALLEL', '5'))
POOL_DAYS = 45

KIND_LABELS = {'cold_first': 'холодный', 'repeat': 'повторный', 'gatekeeper_only': 'секретарь',
               'no_conversation': 'нет разговора', 'not_sales': 'не продажа', 'other_product': 'не про Averon'}
CHANCE_LABELS = {'hot': 'горячий шанс', 'normal': 'шанс был', 'none': 'шанса не было'}


async def pick_calls(pool, client_id: int, limit: int = CALLS) -> list[int]:
    """Последние разобранные звонки с расшифровкой: половина с результатом, половина без.
    Набор не случайный: два прогона подряд берут те же звонки, пока не пришли новые."""
    rows = await pool.fetch(
        f"""SELECT r.call_id, (r.data->>'success')='true' AS ok FROM call_reviews r JOIN calls c ON c.id=r.call_id
            WHERE r.client_id=$1 AND (r.data->>'reviewed')='true' AND coalesce(c.transcript,'')<>''
              AND c.call_started_at > now() - interval '{POOL_DAYS} days'
            ORDER BY c.call_started_at DESC NULLS LAST, r.call_id DESC LIMIT 400""", client_id)
    good = [r['call_id'] for r in rows if r['ok']]
    bad = [r['call_id'] for r in rows if not r['ok']]
    half = limit // 2
    take = good[:half] + bad[:limit - min(half, len(good))]
    take += good[half:half + limit - len(take)]
    return take[:limit]


def summary(data: dict | None) -> dict:
    """Итог разбора в сравнимом виде."""
    if not isinstance(data, dict):
        return {'kind': None, 'success': None, 'outcome': [], 'chance': None, 'category': None}
    moment = data.get('key_moment') if isinstance(data.get('key_moment'), dict) else {}
    return {'kind': data.get('kind'), 'success': bool(data.get('success')),
            'outcome': sorted(data.get('outcome') or []), 'chance': data.get('chance'),
            'category': moment.get('category') if data.get('reviewed') and not data.get('success') else None}


def label(s: dict | None) -> str:
    """Одной строкой, как в сводке: «холодный · без результата, шанс был · не закреплён день перезвона»."""
    from .render import CATEGORY_LABELS, OUTCOME_LABELS
    if not s or s.get('kind') is None:
        return 'сбой'
    parts = [KIND_LABELS.get(s['kind'], s['kind'])]
    if s['kind'] not in engine.REVIEWED:
        return parts[0]
    if s['success']:
        parts.append('результат: ' + ', '.join(OUTCOME_LABELS.get(o, o) for o in s['outcome']))
    else:
        parts.append('без результата, ' + CHANCE_LABELS.get(s['chance'], 'шанс не указан'))
        if s['category']:
            parts.append(CATEGORY_LABELS.get(s['category'], s['category']))
    return ' · '.join(parts)


def changed(before: dict, after: dict) -> list[str]:
    """Что поменялось в итоге. Пустой список — то же самое."""
    if before.get('kind') is None or after.get('kind') is None:
        return ['сбой'] if before.get('kind') != after.get('kind') else []
    fields = []
    if before['kind'] != after['kind']:
        fields.append('тип')
    if before['success'] != after['success'] or before['outcome'] != after['outcome']:
        fields.append('результат')
    elif not after['success']:
        if before['chance'] != after['chance']:
            fields.append('шанс')
        if before['category'] != after['category']:
            fields.append('категория')
    return fields


async def check(pool, client_id: int, new_body: str, run=None, limit: int = CALLS) -> dict:
    """Прогон действующего общего промта и нового на одних и тех же звонках."""
    from . import store
    run = run or engine.run
    old_body, old_version = await prompts.common(pool, 'review', client_id)
    old_prompt, new_prompt = prompts.full_review(old_body), prompts.full_review(new_body)
    calls = await pick_calls(pool, client_id, limit)
    gate = asyncio.Semaphore(PARALLEL)
    cost = 0.0

    async def one(call_id: int, prompt: str):
        nonlocal cost
        async with gate:
            row = await store.load(pool, call_id)
            transcript = await pool.fetchval('SELECT transcript FROM calls WHERE id=$1', call_id)
            try:
                data, spent = await run(transcript or '', await prompts.review_meta(pool, row), prompt=prompt)
                cost += spent or 0.0
                return row, data, prompts.broken_review(data)
            except Exception as exc:
                log.warning('проверка промта: звонок %s не разобран: %s', call_id, exc)
                return row, None, prompts.short_error(exc)

    results = await asyncio.gather(*[one(c, p) for c in calls for p in (old_prompt, new_prompt)])
    items = []
    for i, call_id in enumerate(calls):
        (row, old, old_err), (_, new, new_err) = results[2 * i], results[2 * i + 1]
        before, after = summary(None if old_err else old), summary(None if new_err else new)
        meta = row['meta'] if row else {}
        started = meta.get('call_started_at')
        items.append({'call_id': call_id, 'manager': meta.get('manager') or (f"доб. {row['extension']}" if row else ''),
                      'date': started.strftime('%d.%m %H:%M') if started else '',
                      'before': label(before), 'after': label(after), 'changed': changed(before, after),
                      'before_ok': before['success'], 'after_ok': after['success'],
                      'error': new_err})
    await prompts.add_spend(pool, client_id, cost)
    return {'old_version': old_version, 'items': items, 'cost': cost, **totals(items)}


def totals(items: list[dict]) -> dict:
    count = collections.Counter(f for item in items for f in item['changed'])
    return {'total': len(items), 'same': sum(1 for i in items if not i['changed']),
            'diff': sum(1 for i in items if i['changed']),
            'broken': sum(1 for i in items if i['error']),
            'success_before': sum(1 for i in items if i['before_ok']),
            'success_after': sum(1 for i in items if i['after_ok']),
            'fields': dict(count)}


def big_shift(report: dict) -> bool:
    shift = report['success_after'] - report['success_before']
    return abs(shift) >= max(2, report['total'] // 5)


def level(report: dict) -> str:
    """Цвет вывода в CRM: good — правка ничего не сломала, warn — посмотреть строки, bad — сбои."""
    if report['broken']:
        return 'bad'
    if not report['total'] or big_shift(report) or report['diff'] > max(2, report['total'] // 10):
        return 'warn'
    return 'good'


def verdict(report: dict) -> str:
    """Одна строка вывода. Пороги грубые: это подсказка, решает человек."""
    if not report['total']:
        return 'Нет разобранных звонков с расшифровкой за последние недели: проверять не на чем.'
    if report['broken']:
        return (f"Новый текст не сработал на {report['broken']} из {report['total']} звонков, поэтому для всех его сохранить "
                "нельзя. Если это сбой модели, запустите проверку ещё раз; если нет, поправьте текст.")
    if big_shift(report):
        word = 'больше' if report['success_after'] > report['success_before'] else 'меньше'
        return f"Внимание: успешных звонков стало заметно {word}: {report['success_before']} → {report['success_after']}. Проверьте, так ли задумано."
    if report['diff'] <= max(2, report['total'] // 10):
        return 'Итоги почти не поменялись: правка ничего не сломала.'
    return f"Итог поменялся в {report['diff']} звонках из {report['total']}. Посмотрите строки ниже, так ли задумано."


def message(report: dict) -> str:
    """Отчёт для Telegram (HTML)."""
    head = [f"<b>Проверка на {report['total']} звонках</b>",
            f"Было: действующий общий промт{' (сохранение #' + str(report['old_version']) + ')' if report['old_version'] else ' (исходный)'}. "
            'Стало: новый текст. Оба прогнаны сейчас на одних и тех же звонках.', '',
            f"Без изменений: {report['same']}, изменился итог: {report['diff']}, сбоев: {report['broken']}.",
            f"Звонков с результатом: было {report['success_before']}, стало {report['success_after']}.",
            html.escape(verdict(report))]
    lines = []
    for item in report['items']:
        if not item['changed']:
            continue
        lines.append(f"\n<b>{html.escape(item['manager'])}, {item['date']}</b> · {', '.join(item['changed'])}\n"
                     f"было: {html.escape(item['before'])}\nстало: {html.escape(item['after'])}"
                     + (f"\nсбой: {html.escape(item['error'])}" if item['error'] else ''))
    if lines:
        head += ['', '<b>Где поменялось</b>'] + lines
    head += ['', 'Модель отвечает не всегда одинаково, поэтому пара изменений бывает и без правки.']
    return '\n'.join(head)
