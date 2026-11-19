ALTER TABLE crm_personal_google_states ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'read' CHECK (purpose IN ('read','mail_send'));
-- Dormant; requires migrations 049 and 058. No action is scheduled automatically.
CREATE TABLE IF NOT EXISTS crm_personal_google_write_grants (
 telegram_user_id BIGINT NOT NULL, client_id BIGINT NOT NULL, kind TEXT NOT NULL CHECK(kind='mail'),
 draft_version INTEGER NOT NULL, google_sub TEXT NOT NULL, email TEXT NOT NULL,
 scope TEXT NOT NULL CHECK(scope='https://www.googleapis.com/auth/gmail.send'),
 refresh_enc TEXT NOT NULL, grant_version TEXT NOT NULL,
 PRIMARY KEY (telegram_user_id, client_id, kind),
 FOREIGN KEY (telegram_user_id,client_id,kind) REFERENCES crm_personal_connections(telegram_user_id,client_id,kind) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS crm_personal_google_actions (
 id TEXT PRIMARY KEY, telegram_user_id BIGINT NOT NULL, client_id BIGINT NOT NULL,
 kind TEXT NOT NULL CHECK(kind='gmail_send'), connection_version INTEGER NOT NULL,
 grant_version TEXT NOT NULL, google_sub TEXT NOT NULL, email TEXT NOT NULL,
 payload_enc TEXT NOT NULL, payload_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('prepared','sending','sent','rejected','unknown')),
 provider_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '15 minutes', completed_at TIMESTAMPTZ
);
