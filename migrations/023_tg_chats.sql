-- Переписка рабочего Telegram (review/tgchats.py). Бот читает аккаунт как отдельное
-- устройство и только читает: ничего не отправляет и не отмечает прочитанным.
-- tg_chats — чат с клиентом: в каких папках лежит, чей клиент (по папке), разбор.
-- tg_messages — текст сообщений; медиа не скачиваются, отмечается только их вид.
CREATE TABLE IF NOT EXISTS tg_chats (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    peer_id BIGINT NOT NULL,
    kind TEXT,  -- user, group, channel
    title TEXT,
    username TEXT,
    phone TEXT,
    folders TEXT[] NOT NULL DEFAULT '{}',
    extension TEXT,  -- менеджер по папке
    last_message_id BIGINT NOT NULL DEFAULT 0,
    last_message_at TIMESTAMPTZ,
    analysis JSONB,
    analyzed_at TIMESTAMPTZ,
    call_id BIGINT REFERENCES calls(id) ON DELETE SET NULL,  -- звонок, через который чат связан со сделкой
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, peer_id)
);
CREATE TABLE IF NOT EXISTS tg_messages (
    client_id INTEGER NOT NULL REFERENCES clients(id),
    peer_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL,
    outgoing BOOLEAN NOT NULL,  -- true: написали мы (менеджер)
    text TEXT,
    media TEXT,  -- photo, document, voice и т.п., если было вложение
    PRIMARY KEY (client_id, peer_id, message_id)
);
CREATE INDEX IF NOT EXISTS tg_messages_time_idx ON tg_messages(client_id, peer_id, sent_at);
