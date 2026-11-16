CREATE TABLE IF NOT EXISTS crm_personal_telegram_events (
 event_key TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS crm_personal_telegram_state (
 telegram_user_id BIGINT PRIMARY KEY, action TEXT NOT NULL, task_id BIGINT,
 version INTEGER, token TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
