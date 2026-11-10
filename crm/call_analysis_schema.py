"""Аддитивная схема разбора звонков.

Штатная выкладка CRM переносит только код и не запускает SQL-миграции,
поэтому таблицы создаются из самого CRM при первом обращении к экрану.
Запросы только добавляют недостающие таблицы и индексы; существующие данные
не меняются. Если у пользователя БД нет права CREATE, ошибка останется
локальной для раздела и не затронет остальную CRM.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)
_lock = asyncio.Lock()

STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS call_analysis_runs (
        id BIGSERIAL PRIMARY KEY,
        client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        operator TEXT NOT NULL,
        extension TEXT,
        kind TEXT NOT NULL,
        source TEXT NOT NULL,
        period_start DATE NOT NULL,
        period_end DATE NOT NULL,
        period_start_at TIMESTAMPTZ NOT NULL,
        period_end_at TIMESTAMPTZ NOT NULL,
        dedup_key TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        stage TEXT NOT NULL DEFAULT '',
        total_calls INTEGER NOT NULL DEFAULT 0,
        processed INTEGER NOT NULL DEFAULT 0,
        result JSONB,
        error TEXT,
        author_tg BIGINT,
        author_ext TEXT,
        author_name TEXT,
        saved_at TIMESTAMPTZ,
        saved_by TEXT,
        telegram_at TIMESTAMPTZ,
        telegram_by BIGINT,
        telegram_error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        UNIQUE (client_id, dedup_key)
    )""",
    """CREATE INDEX IF NOT EXISTS call_analysis_runs_client_idx
       ON call_analysis_runs(client_id, created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS call_analysis_runs_queue_idx
       ON call_analysis_runs(created_at) WHERE status='queued'""",
    """CREATE TABLE IF NOT EXISTS call_analysis_items (
        id BIGSERIAL PRIMARY KEY,
        run_id BIGINT NOT NULL REFERENCES call_analysis_runs(id) ON DELETE CASCADE,
        client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        call_ref TEXT NOT NULL,
        call_id BIGINT,
        phone TEXT,
        at TIMESTAMPTZ,
        duration_s INTEGER,
        status TEXT NOT NULL DEFAULT 'pending',
        transcript_source TEXT,
        transcript_confidence NUMERIC(5,4),
        transcript_excerpt TEXT,
        stages JSONB NOT NULL DEFAULT '{}'::jsonb,
        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        lead_source TEXT,
        lead_id TEXT,
        lead_label TEXT,
        unknown_attribution BOOLEAN NOT NULL DEFAULT true,
        break_group TEXT,
        break_code TEXT,
        break_detail TEXT,
        model_lead_hint BOOLEAN NOT NULL DEFAULT false,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        processed_at TIMESTAMPTZ,
        UNIQUE (run_id, call_ref)
    )""",
    """CREATE INDEX IF NOT EXISTS call_analysis_items_run_idx
       ON call_analysis_items(run_id, status)""",
    """CREATE INDEX IF NOT EXISTS call_analysis_items_phone_idx
       ON call_analysis_items(client_id, phone)""",
)


async def ensure(pool) -> None:
    """Создать недостающие таблицы один раз на процесс."""
    if await pool.fetchval(
            "SELECT to_regclass('call_analysis_runs') IS NOT NULL AND to_regclass('call_analysis_items') IS NOT NULL"):
        return
    async with _lock:
        if await pool.fetchval(
                "SELECT to_regclass('call_analysis_runs') IS NOT NULL AND to_regclass('call_analysis_items') IS NOT NULL"):
            return
        log.info('разбор звонков: создаю недостающие таблицы')
        async with pool.acquire() as conn:
            for statement in STATEMENTS:
                await conn.execute(statement)
