CREATE TABLE IF NOT EXISTS crm_personal_connections (
 telegram_user_id BIGINT NOT NULL, client_id BIGINT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('mail','calendar')),
 provider TEXT NOT NULL CHECK (provider IN ('yandex','google','microsoft','other')),
 email TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '', calendar_name TEXT NOT NULL DEFAULT '',
 timezone TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(telegram_user_id,client_id,kind)
);
