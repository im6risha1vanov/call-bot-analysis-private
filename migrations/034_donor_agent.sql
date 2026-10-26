-- Агент доноров (crm/donor_agent.py, static/donors.js): прогоны поиска доноров на сервере.
-- Менеджер задаёт нишу и географию, агент сам строит план, читает выдачу Яндекса и сайты,
-- проверяет номера и сохраняет итог. state — рабочие данные прогона (кандидаты, досье, найденные номера).
CREATE TABLE IF NOT EXISTS donor_runs (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    niche TEXT NOT NULL,
    city TEXT NOT NULL DEFAULT '',
    offer TEXT NOT NULL DEFAULT '',
    project_client TEXT NOT NULL DEFAULT '',  -- сайт или название клиента: его самого донором не берём
    target INTEGER NOT NULL DEFAULT 20,  -- сколько проверенных номеров нужно
    status TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed | stopped
    stage TEXT NOT NULL DEFAULT '',
    error TEXT,
    stop_requested BOOLEAN NOT NULL DEFAULT false,
    plan JSONB,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,  -- итог: номера с вердиктами и сайты
    log JSONB NOT NULL DEFAULT '[]'::jsonb,
    usd NUMERIC(10,4) NOT NULL DEFAULT 0,
    searches INTEGER NOT NULL DEFAULT 0,
    author_tg BIGINT,
    author_ext TEXT,
    author_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS donor_runs_client_idx ON donor_runs(client_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS donor_runs_queue_idx ON donor_runs(created_at) WHERE status IN ('queued', 'running');

-- Расход агента за день по всем компаниям (ИИ и платный поиск), в долларах: общий дневной лимит DONOR_DAILY_USD.
CREATE TABLE IF NOT EXISTS donor_spend (
    day DATE PRIMARY KEY,
    usd NUMERIC(10,4) NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    searches INTEGER NOT NULL DEFAULT 0
);
