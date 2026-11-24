-- Одноразовые ссылки-приглашения в бота (invites.py, scripts/invite.sh):
-- t.me/<бот>?start=inv_<code> привязывает того, кто открыл ссылку, к строке employees.
CREATE TABLE IF NOT EXISTS employee_invites (
    code TEXT PRIMARY KEY,
    client_id INTEGER NOT NULL,
    extension TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '7 days',
    used_at TIMESTAMPTZ,
    used_by BIGINT
);
