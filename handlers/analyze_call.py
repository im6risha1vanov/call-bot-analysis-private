"""
Обработчик типа задачи `analyze_call` — регистрируется в queue_runner при
импорте модуля. Логика перенесена из прежнего astra_worker.process_call()
без изменений по существу: раннер отвечает за очередь и повторы, обработчик —
только за сам разбор звонка и доставку. Задачи сюда кладёт
astra_worker.poll_client_calls() вместо прежнего прямого вызова
claim_next()/process_call() на таблице calls.

Число запросов к модели, критерии и их веса не менялись — см. analysis.py.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
from aiogram import Bot

import mango_client
from analysis import score_call, short_report_call
from crypto_util import decrypt
from deepgram_client import transcribe_bytes
from queue_runner import RetryLater, on_startup, register
from review import callbacks as review_callbacks, engine as review_engine, kitchen as review_kitchen, store as review_store

log = logging.getLogger("callbot-astra-worker.analyze_call")

BUDGET_RECHECK_MIN = 30
DG_PRICE_PER_MIN_USD = 0.0043  # nova-3, предзаписанное аудио — только для лога
DAILY_UNIT_LIMIT = float(os.getenv("MAX_COST_PER_CHAT_UNITS", "2000000"))

_bot = Bot(token=os.environ["BOT_TOKEN"])


async def _over_daily_limit(pool: asyncpg.Pool, client: asyncpg.Record) -> bool:
    from automatic.jobs import installed, over_budget
    if await installed(pool):
        return await over_budget(pool, client)
    spent = await pool.fetchval(
        "SELECT spent_units FROM astra_daily_spend WHERE client_id=$1 AND day=$2",
        client["id"], datetime.now(ZoneInfo(client["timezone"])).date(),
    )
    return float(spent or 0) >= DAILY_UNIT_LIMIT


async def _add_spend(pool: asyncpg.Pool, client: asyncpg.Record, units: float) -> None:
    await pool.execute(
        """
        INSERT INTO astra_daily_spend (client_id, day, spent_units) VALUES ($1, $2, $3)
        ON CONFLICT (client_id, day) DO UPDATE SET spent_units = astra_daily_spend.spent_units + EXCLUDED.spent_units
        """,
        client["id"], datetime.now(ZoneInfo(client["timezone"])).date(), units,
    )


async def _enqueue_variants(pool,client,call,transcript,level):
    async with pool.acquire() as conn, conn.transaction():
        from methodology.jobs import enqueue as enqueue_course
        await enqueue_course(conn, call['client_id'], 'call', call['id'], transcript)
        if level is not None:
            from automatic.jobs import enqueue as enqueue_combined
            from reports import fmt_phone
            manager_name = await conn.fetchval("SELECT full_name FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'", call['client_id'], call['extension'])
            await enqueue_combined(conn,call['client_id'],call['id'],transcript,{
                'call_id':call['id'],'manager':manager_name or f"доб. {call['extension']}",
                'extension':call['extension'], 'duration_seconds':call['duration_seconds'] or 0,
                'call_started_at':call['call_started_at'].astimezone(ZoneInfo(client['timezone'])).isoformat() if call['call_started_at'] else None,
                'timezone':client['timezone'],'phone':fmt_phone(call['client_number']) or 'неизвестен',
            })


async def _review_meta(pool: asyncpg.Pool, client: asyncpg.Record, call: asyncpg.Record) -> dict:
    tz = ZoneInfo(client["timezone"])
    manager = await pool.fetchval("SELECT full_name FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'",
                                  call["client_id"], call["extension"])
    seconds = int(call["duration_seconds"] or 0)
    return {"manager": manager or f"доб. {call['extension']}",
            "date": call["call_started_at"].astimezone(tz).strftime("%d.%m.%Y %H:%M") if call["call_started_at"] else None,
            "duration": f"{seconds // 60}:{seconds % 60:02d}" if seconds else None,
            "previous": await review_store.previous(pool, call["client_id"], call["client_number"], call["id"])}


@on_startup
async def _reset_orphaned(pool: asyncpg.Pool) -> None:
    """Единственное место, отвечающее за статус 'processing' в calls/
    astra_analysis — раньше этим владел astra_worker, теперь владелец этого
    состояния — обработчик analyze_call."""
    result = await pool.execute("UPDATE calls SET status='new', updated_at=now() WHERE status='processing'")
    if result != "UPDATE 0":
        log.warning("восстановлены зависшие processing-строки calls после перезапуска: %s", result)
    result = await pool.execute("UPDATE astra_analysis SET status='new' WHERE status='processing'")
    if result != "UPDATE 0":
        log.warning("восстановлены зависшие processing-строки astra_analysis после перезапуска: %s", result)


@register("analyze_call")
async def analyze_call(pool: asyncpg.Pool, task: asyncpg.Record) -> dict:
    payload = json.loads(task["input"])
    from unified.analysis_policy import pin_task
    package = await pin_task(pool, task, payload)
    call = await pool.fetchrow("SELECT * FROM calls WHERE id=$1", payload["call_id"])
    if call is None:
        raise RuntimeError(f"calls.id={payload['call_id']} не найден")
    if call['client_id'] != task['client_id']:
        raise ValueError('Задание и звонок относятся к разным клиентам.')
    client = await pool.fetchrow("SELECT * FROM clients WHERE id=$1", call["client_id"])

    if not client['processing_enabled']:
        raise RetryLater(timedelta(minutes=BUDGET_RECHECK_MIN), 'обработка клиента отключена')

    prior = await pool.fetchrow('SELECT * FROM astra_analysis WHERE call_id=$1',call['id'])
    cached = call['status']=='analyzed' and prior and prior['status']=='analyzed' and prior['short_report'] is not None
    if cached:
        transcript = call['transcript'] or ''
        short_report = json.loads(prior['short_report'])
        level, cost = prior['level'], float(prior['cost_units'] or 0)
    else:
        if await _over_daily_limit(pool, client):
            raise RetryLater(timedelta(minutes=BUDGET_RECHECK_MIN), "дневной лимит клиента исчерпан")

        await pool.execute("UPDATE calls SET status='processing', updated_at=now() WHERE id=$1", call["id"])
        await pool.execute(
            "INSERT INTO astra_analysis (call_id, status) VALUES ($1,'processing') "
            "ON CONFLICT (call_id) DO UPDATE SET status='processing'",
            call["id"],
        )

        try:
            vpbx_api_key = decrypt(client["vpbx_api_key_enc"])
            vpbx_api_salt = decrypt(client["vpbx_api_salt_enc"])

            audio = await mango_client.fetch_recording(vpbx_api_key, vpbx_api_salt, call["recording_id"])
            transcript, dg_duration = await transcribe_bytes(audio)
            del audio

            kitchen = None
            if review_kitchen.handles(call['extension']):
                # Оператор прозвона лидов работает по своему скрипту: итог попытки
                # идёт в его таблицу (review/kitchen.py), разбора Averon нет.
                kitchen, cost = await review_kitchen.run(transcript)
                analysis, score, level, review = kitchen, None, review_kitchen.level(kitchen), None
                short_report = {"result": kitchen.get("comment"), "good": [], "weak": []}
            elif review_engine.enabled():
                # Разбор по результату: один запрос вместо оценки и короткого
                # отчёта. Балла нет; level нужен только для статистики.
                review, cost = await review_engine.run(transcript, await _review_meta(pool, client, call))
                analysis, score, level = review, None, review_engine.level(review)
                short_report = {"result": review.get("rop_line"), "good": [], "weak": []}
            else:
                review = None
                scores, score, level, rows, cost = await score_call(transcript)
                from unified.analysis_policy import evaluate
                score, level, rows = evaluate(scores, package)
                short_report, short_cost = await short_report_call(transcript, scores, rows, level)
                cost += short_cost
                analysis = {**scores, "rows": rows}

            await _add_spend(pool, client, cost)
            log.info("Deepgram (справочно, не в бюджете Astra): $%.4f", dg_duration / 60 * DG_PRICE_PER_MIN_USD)
        except Exception:
            log.exception("ошибка обработки call id=%s (задача id=%s)", call["id"], task["id"])
            await pool.execute("UPDATE calls SET status='new', updated_at=now() WHERE id=$1", call["id"])
            await pool.execute("UPDATE astra_analysis SET status='new' WHERE call_id=$1", call["id"])
            raise

        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE calls SET transcript=$2, status='analyzed', updated_at=now() WHERE id=$1",
                call["id"], transcript,
            )
            await conn.execute(
            """
            UPDATE astra_analysis SET
                status='analyzed', analysis=$2::jsonb, score=$3, level=$4, short_report=$5::jsonb,
                cost_units=$6, updated_at=now()
            WHERE call_id=$1
            """,
            call["id"], json.dumps(analysis, ensure_ascii=False), score, level,
            json.dumps(short_report, ensure_ascii=False), cost,
            )
            if kitchen is not None:
                await review_kitchen.record(conn, call, kitchen, cost)
            if review is not None:
                await review_store.save(conn, call, review, cost)
                await review_callbacks.record(conn, call, review)
    if review_kitchen.handles(call['extension']):
        log.info("call id=%s: попытка прозвона лидов, итог=%s", call["id"], short_report.get("result"))
        return {"call_id": call["id"], "level": level, "cost_units": cost, "cached": bool(cached), "kitchen": True}
    if review_engine.enabled() and await pool.fetchval('SELECT 1 FROM call_reviews WHERE call_id=$1', call['id']):
        sent = await review_store.deliver_now(pool, _bot, call['id'])
        log.info("call id=%s разобран (разбор по результату): уровень=%s стоимость=%.0f ед.", call["id"], level, cost)
        return {"call_id": call["id"], "level": level, "cost_units": cost, "cached": bool(cached), "review_sent": sent}
    # A queue retry refers to the same immutable run. An explicit new task is
    # another run. Already-delivered pre-upgrade jobs are not historical backfill.
    from unified import store as unified_store
    legacy_delivered = cached and (prior['immediate_sent_manager'] or prior['immediate_sent_head'])
    run_id = await unified_store.capture(pool, call['id'], call['client_id'], f"task:{task['id']}", deliver=not legacy_delivered, fresh=not cached, package=package)
    enqueue_error = False
    try:
        if not package.get('isolate_primary'):
            await _enqueue_variants(pool,client,call,transcript,level)
    except Exception:
        log.exception('не удалось поставить дополнительные разборы, call id=%s',call['id'])
        enqueue_error = True
    log.info("call id=%s разобран через очередь: уровень=%s стоимость=%.0f ед.", call["id"], level, cost)

    if enqueue_error:
        raise RetryLater(timedelta(seconds=20),'повтор постановки дополнительных разборов без повторной оплаты исходного')
    return {"call_id": call["id"], "level": level, "cost_units": cost, "cached": bool(cached), "run_id": run_id}
