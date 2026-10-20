-- Разбор презентаций (review/presentations.py): записи из реестра Григория
-- «Реестр звонков с клиентами» (Google-таблица, mp3 на Google Диске). Одна строка на
-- файл записи. call_id — звонок Mango, с которым запись совпала по менеджеру, дню и
-- длительности (сверка текстом в match); по нему находится номер клиента и сделка.
CREATE TABLE IF NOT EXISTS presentations (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    file_id TEXT NOT NULL,
    row_no INTEGER,
    day DATE,
    time_text TEXT,
    manager_name TEXT,
    extension TEXT,
    comment TEXT,
    status TEXT NOT NULL DEFAULT 'new',  -- new, reviewed, skipped, error
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    duration_seconds INTEGER,
    transcript TEXT,
    call_id BIGINT REFERENCES calls(id) ON DELETE SET NULL,
    match JSONB,
    data JSONB,
    prompt_version TEXT,
    cost_units NUMERIC(14,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, file_id)
);
CREATE INDEX IF NOT EXISTS presentations_status_idx ON presentations(status, id);
CREATE INDEX IF NOT EXISTS presentations_call_idx ON presentations(call_id);
