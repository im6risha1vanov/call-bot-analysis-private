-- Сотрудники и режим промта в CRM (crm/staff.py, crm/prompts.py).
-- Личная ссылка директора может принадлежать конкретному человеку из бота:
-- тогда в CRM он работает под своим Telegram ID (личные промты, «кто изменил»).
ALTER TABLE crm_invites ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT;
-- Ник уже есть на сервере (scripts/invite.sh); здесь на случай чистой базы.
ALTER TABLE employees ADD COLUMN IF NOT EXISTS telegram_username TEXT;
-- Кому уходят полные копии разборов. Пока строки нет, действует REVIEW_COPY_TO из .env;
-- первое изменение в «Сотрудниках» переносит список сюда, и дальше он правится без перезапуска.
CREATE TABLE IF NOT EXISTS review_copy_lists (
 client_id INTEGER PRIMARY KEY REFERENCES clients(id),
 tokens TEXT[] NOT NULL DEFAULT '{}',
 updated_by BIGINT,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
