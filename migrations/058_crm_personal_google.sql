CREATE TABLE IF NOT EXISTS crm_personal_google_states (
 state_hash TEXT PRIMARY KEY, telegram_user_id BIGINT NOT NULL, client_id BIGINT NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('mail','calendar')), purpose TEXT NOT NULL DEFAULT 'read' CHECK (purpose IN ('read','mail_send')), draft_version INTEGER NOT NULL,
 email TEXT NOT NULL, verifier_enc TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_personal_google_tokens (
 telegram_user_id BIGINT NOT NULL, client_id BIGINT NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('mail','calendar')),
 draft_version INTEGER NOT NULL, google_sub TEXT NOT NULL, email TEXT NOT NULL,
 token_enc TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY (telegram_user_id, client_id, kind),
 FOREIGN KEY (telegram_user_id, client_id, kind) REFERENCES crm_personal_connections (telegram_user_id, client_id, kind) ON DELETE CASCADE
);
