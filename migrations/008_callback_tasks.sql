-- Callbacks agreed in reviewed calls. The database is the source of truth;
-- Telegram buttons change status, Google Sheets only mirrors it.
CREATE TABLE IF NOT EXISTS callback_tasks (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id),
 extension TEXT,
 call_id BIGINT NOT NULL REFERENCES calls(id),
 client_number TEXT,
 company TEXT,
 kind TEXT NOT NULL,
 due_date DATE NOT NULL,
 due_time TIME,
 date_is_estimate BOOLEAN NOT NULL DEFAULT false,
 raw TEXT,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done','cancelled','closed_by_call')),
 closed_call_id BIGINT REFERENCES calls(id),
 reminded_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(call_id)
);
CREATE INDEX IF NOT EXISTS callback_tasks_due_idx ON callback_tasks(client_id,status,due_date);
CREATE INDEX IF NOT EXISTS callback_tasks_number_idx ON callback_tasks(client_id,client_number) WHERE status='open';
CREATE TABLE IF NOT EXISTS callback_digest_state (
 client_id INTEGER PRIMARY KEY REFERENCES clients(id),
 last_morning_date DATE
);
