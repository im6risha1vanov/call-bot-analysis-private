-- Многоклиентная CRM: вкладка «Компании» для нас (платформа). Отметка platform у учётной записи
-- нашей компании открывает список компаний; ставит её crm_setup.sh platform <логин>.
ALTER TABLE crm_invites ADD COLUMN IF NOT EXISTS platform BOOLEAN NOT NULL DEFAULT false;
-- Вход в компанию для поддержки: только просмотр, с отметкой, кто вошёл.
ALTER TABLE crm_sessions ADD COLUMN IF NOT EXISTS support_by BIGINT REFERENCES crm_invites(id) ON DELETE CASCADE;
CREATE TABLE IF NOT EXISTS platform_log (
 id BIGSERIAL PRIMARY KEY,
 at TIMESTAMPTZ NOT NULL DEFAULT now(),
 invite_id BIGINT REFERENCES crm_invites(id) ON DELETE SET NULL,
 client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
 action TEXT NOT NULL,
 detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS platform_log_at_idx ON platform_log(at DESC);
