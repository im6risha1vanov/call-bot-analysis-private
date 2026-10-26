-- Многоклиентная CRM: настройки компании (clients = компания). Название, включённые модули и то,
-- что раньше задавалось одним на сервер в .env (таблица колл-центра, e-mail в договорах).
-- Нет строки у нашей компании — всё как раньше из .env (crm/company.py).
CREATE TABLE IF NOT EXISTS company_settings (
 client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
 name TEXT,
 modules TEXT[],  -- NULL: по умолчанию (у нашей все, у новой DEFAULT_MODULES)
 settings JSONB NOT NULL DEFAULT '{}'::jsonb,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
