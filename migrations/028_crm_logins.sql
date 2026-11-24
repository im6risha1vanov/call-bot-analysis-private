-- Вход по логину и паролю: учётная запись — это личный доступ из «Доступов» с логином.
-- Для входа сверяем хэш scrypt; директор видит пароль в «Доступах», поэтому рядом он лежит
-- зашифрованным ключом ENCRYPTION_KEY (как ключи Манго). Отзыв доступа закрывает его сессии.
ALTER TABLE crm_invites ADD COLUMN IF NOT EXISTS login TEXT;
ALTER TABLE crm_invites ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE crm_invites ADD COLUMN IF NOT EXISTS password_enc TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS crm_invites_login_idx ON crm_invites(lower(login)) WHERE login IS NOT NULL AND revoked_at IS NULL;
