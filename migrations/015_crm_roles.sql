-- Входы в CRM: директор, менеджер, клиент. Тестовая ссылка выбирает вход
-- без привязки к Telegram, поэтому роль хранится в самой сессии.
ALTER TABLE crm_sessions ALTER COLUMN telegram_user_id DROP NOT NULL;
ALTER TABLE crm_sessions ADD COLUMN IF NOT EXISTS role TEXT;
ALTER TABLE crm_sessions ADD COLUMN IF NOT EXISTS client_id INTEGER;
ALTER TABLE crm_sessions ADD COLUMN IF NOT EXISTS extension TEXT;
ALTER TABLE crm_sessions ADD COLUMN IF NOT EXISTS client_key TEXT;
ALTER TABLE crm_sessions ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;
-- Документы страницы директора (этапы воронки и настройки), раньше лежали в базе страницы claude.ai.
CREATE TABLE IF NOT EXISTS crm_docs (
 path TEXT PRIMARY KEY,
 data JSONB NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
