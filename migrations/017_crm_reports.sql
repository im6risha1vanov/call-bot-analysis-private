-- Отчёты бота (дайджесты РОПа, итоги дня менеджерам) копией в CRM, чтобы их можно было открыть на сайте.
CREATE TABLE IF NOT EXISTS crm_reports (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id),
 kind TEXT NOT NULL,
 title TEXT NOT NULL,
 extension TEXT,
 body TEXT NOT NULL,
 calls JSONB NOT NULL DEFAULT '[]',
 note TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crm_reports_client_idx ON crm_reports(client_id, created_at DESC);
