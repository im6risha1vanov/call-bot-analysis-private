-- Личные ссылки в CRM: директор выдаёт их менеджерам, клиентам и себе в разделе «Доступы».
-- Храним только хэш ссылки; отзыв ссылки закрывает все её сессии.
CREATE TABLE IF NOT EXISTS crm_invites (
 id BIGSERIAL PRIMARY KEY,
 token_hash TEXT NOT NULL UNIQUE,
 role TEXT NOT NULL CHECK(role IN ('director','manager','client')),
 client_id INTEGER NOT NULL REFERENCES clients(id),
 extension TEXT,
 client_key TEXT,
 name TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 last_used_at TIMESTAMPTZ,
 revoked_at TIMESTAMPTZ
);
ALTER TABLE crm_sessions ADD COLUMN IF NOT EXISTS invite_id BIGINT REFERENCES crm_invites(id) ON DELETE CASCADE;
