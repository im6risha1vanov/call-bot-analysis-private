-- AveronGPT: помощник в CRM для директора, менеджера и клиента. Вопросы и ответы в той же таблице,
-- что вопросы агенту РОПа со страницы «Отчёты» (их история переезжает в окно помощника).
ALTER TABLE crm_questions ADD COLUMN IF NOT EXISTS client_key TEXT;
ALTER TABLE crm_questions ADD COLUMN IF NOT EXISTS page TEXT;
ALTER TABLE crm_questions ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE crm_questions ADD COLUMN IF NOT EXISTS cost_rub NUMERIC;
ALTER TABLE crm_questions ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS crm_questions_client_idx ON crm_questions(client_id, role, client_key, created_at DESC);
-- Расход помощника по дням: лимит только у клиентов (scope = 'client:<лист>').
CREATE TABLE IF NOT EXISTS crm_gpt_spend (
 day DATE NOT NULL,
 scope TEXT NOT NULL,
 rub NUMERIC NOT NULL DEFAULT 0,
 PRIMARY KEY (day, scope)
);
