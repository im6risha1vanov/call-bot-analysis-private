-- Вопросы агенту РОПа из CRM и его ответы (раньше только свободный текст в боте).
CREATE TABLE IF NOT EXISTS crm_questions (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id),
 role TEXT NOT NULL,
 extension TEXT,
 question TEXT NOT NULL,
 answer TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crm_questions_idx ON crm_questions(client_id, role, extension, created_at DESC);
