-- Попытки прозвона по скрипту «Кухонные гарнитуры» (review/kitchen.py).
-- Строка на звонок оператора; worker переносит её в таблицу «Прозвон лидов».
CREATE TABLE IF NOT EXISTS lead_attempts (
 call_id BIGINT PRIMARY KEY REFERENCES calls(id),
 client_id INTEGER NOT NULL REFERENCES clients(id),
 extension TEXT,
 client_number TEXT,
 call_started_at TIMESTAMPTZ,
 status TEXT NOT NULL CHECK(status IN ('ndz','callback','not_relevant','interested')),
 comment TEXT,
 data JSONB NOT NULL DEFAULT '{}',
 cost_units NUMERIC NOT NULL DEFAULT 0,
 written_at TIMESTAMPTZ,
 sheet_cell TEXT,
 error TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS lead_attempts_pending_idx ON lead_attempts(call_started_at) WHERE written_at IS NULL;
