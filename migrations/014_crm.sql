-- CRM менеджера на нашем сервере (crm/): вход по ссылке из бота, своя база
-- номеров, звонок через Mango и итог звонка. Только добавляет таблицы и колонки.
CREATE TABLE IF NOT EXISTS crm_login_tokens (
 token_hash TEXT PRIMARY KEY,
 telegram_user_id BIGINT NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 used_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS crm_sessions (
 token_hash TEXT PRIMARY KEY,
 telegram_user_id BIGINT NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Номера, которые менеджер загрузил в CRM для обзвона.
CREATE TABLE IF NOT EXISTS crm_leads (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id),
 extension TEXT NOT NULL,
 number TEXT NOT NULL,
 name TEXT,
 company TEXT,
 source TEXT,
 note TEXT,
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN
   ('new','no_answer','callback','interested','proposal','meeting','not_interested','bad_number')),
 attempts INTEGER NOT NULL DEFAULT 0,
 next_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(client_id, extension, number)
);
CREATE INDEX IF NOT EXISTS crm_leads_queue_idx ON crm_leads(client_id, extension, status, next_at);
-- Каждое нажатие «Позвонить» и ответ Mango.
CREATE TABLE IF NOT EXISTS crm_dials (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id),
 extension TEXT NOT NULL,
 telegram_user_id BIGINT,
 number TEXT NOT NULL,
 command_id TEXT NOT NULL,
 result INTEGER,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crm_dials_ext_idx ON crm_dials(client_id, extension, created_at);
-- Итог, который менеджер отметил после звонка.
CREATE TABLE IF NOT EXISTS crm_outcomes (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id),
 extension TEXT NOT NULL,
 number TEXT NOT NULL,
 outcome TEXT NOT NULL,
 comment TEXT,
 due_date DATE,
 due_time TIME,
 dial_id BIGINT REFERENCES crm_dials(id),
 telegram_user_id BIGINT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crm_outcomes_number_idx ON crm_outcomes(client_id, number, created_at);
CREATE INDEX IF NOT EXISTS crm_outcomes_ext_idx ON crm_outcomes(client_id, extension, created_at);
-- Перезвон, назначенный в CRM, не привязан к разобранному звонку.
ALTER TABLE callback_tasks ALTER COLUMN call_id DROP NOT NULL;
ALTER TABLE callback_tasks ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'review';
-- «Не взял трубку» убирает перезвон из очереди до этого времени.
ALTER TABLE callback_tasks ADD COLUMN IF NOT EXISTS retry_at TIMESTAMPTZ;
