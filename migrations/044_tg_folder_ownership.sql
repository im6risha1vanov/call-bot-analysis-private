-- Старые назначения по названию папки ждут подтверждения новой синхронизацией.
ALTER TABLE tg_chats ADD COLUMN IF NOT EXISTS folder_owner_verified BOOLEAN NOT NULL DEFAULT false;
