-- Многоклиентная CRM: у каждой компании свои промты. Общая версия (owner_tg=0) — на компанию,
-- личные версии и так привязаны к человеку. Все прежние сохранения — нашей компании (первой рабочей).
ALTER TABLE prompt_versions ADD COLUMN IF NOT EXISTS client_id INTEGER REFERENCES clients(id);
ALTER TABLE prompt_active ADD COLUMN IF NOT EXISTS client_id INTEGER REFERENCES clients(id);
UPDATE prompt_versions SET client_id=(SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1) WHERE client_id IS NULL;
UPDATE prompt_active SET client_id=(SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1) WHERE client_id IS NULL;
ALTER TABLE prompt_versions ALTER COLUMN client_id SET NOT NULL;
ALTER TABLE prompt_active ALTER COLUMN client_id SET NOT NULL;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='prompt_active_pkey'
             AND pg_get_constraintdef(oid) NOT LIKE '%client_id%') THEN
    ALTER TABLE prompt_active DROP CONSTRAINT prompt_active_pkey;
    ALTER TABLE prompt_active ADD PRIMARY KEY (client_id, kind, owner_tg);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS prompt_versions_company_idx ON prompt_versions(client_id, kind, created_at DESC);
