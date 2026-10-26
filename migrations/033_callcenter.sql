-- Многоклиентная CRM, этап 5: колл-центр без Google Таблицы. Компания без таблицы колл-центра ведёт
-- клиентов и номера здесь; кабинет клиента и сводка считаются так же, как по листам таблицы.
CREATE TABLE IF NOT EXISTS cc_clients (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
 name TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (client_id, name)
);
CREATE TABLE IF NOT EXISTS cc_leads (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
 cc_client_id BIGINT NOT NULL REFERENCES cc_clients(id) ON DELETE CASCADE,
 phone TEXT NOT NULL,
 tag TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '',
 comment TEXT NOT NULL DEFAULT '',
 remind TEXT NOT NULL DEFAULT '',
 base_date DATE,
 -- попытки по порядку: [{"r": "НДЗ"|"Перезвон"|"Заинтересованный"|"Неактуально", "d": "2026-10-24", "by": "101"}]
 attempts JSONB NOT NULL DEFAULT '[]'::jsonb,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (cc_client_id, phone)
);
CREATE INDEX IF NOT EXISTS cc_leads_client_idx ON cc_leads(client_id, cc_client_id);
