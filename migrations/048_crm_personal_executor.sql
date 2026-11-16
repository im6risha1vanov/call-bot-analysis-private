CREATE TABLE IF NOT EXISTS crm_personal_agent_jobs (
 id BIGSERIAL PRIMARY KEY, telegram_user_id BIGINT NOT NULL, client_id BIGINT NOT NULL,
 request TEXT NOT NULL, event_key TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'queued',
 result TEXT, error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 delivery_status TEXT NOT NULL DEFAULT 'none', attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS crm_personal_agent_jobs_owner ON crm_personal_agent_jobs(telegram_user_id,client_id,id DESC);
CREATE TABLE IF NOT EXISTS crm_personal_agent_steps (
 id BIGSERIAL PRIMARY KEY, job_id BIGINT NOT NULL REFERENCES crm_personal_agent_jobs(id),
 sequence INTEGER NOT NULL, tool TEXT NOT NULL, status TEXT NOT NULL, evidence JSONB NOT NULL DEFAULT '{}',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(job_id,sequence)
);
CREATE TABLE IF NOT EXISTS crm_personal_agent_actions (
 job_id BIGINT PRIMARY KEY REFERENCES crm_personal_agent_jobs(id), token TEXT NOT NULL UNIQUE,
 recipient BIGINT NOT NULL, recipient_name TEXT NOT NULL, sender_name TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed',
 message_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE crm_personal_agent_actions ADD COLUMN IF NOT EXISTS recipient_name TEXT NOT NULL DEFAULT '';
ALTER TABLE crm_personal_agent_actions ADD COLUMN IF NOT EXISTS sender_name TEXT NOT NULL DEFAULT '';
