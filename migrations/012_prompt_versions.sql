-- Режим промта (review/prompts.py, review/prompt_ui.py): сохранения промтов списком,
-- какой из них сейчас действует, и черновик правки в диалоге с ботом.

-- Каждое сохранение: тело промта целиком. owner_tg = 0 — общая версия «для всех»,
-- иначе личная версия этого человека. Строки не удаляются: это список сохранений.
CREATE TABLE IF NOT EXISTS prompt_versions (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    owner_tg BIGINT NOT NULL DEFAULT 0,
    body TEXT NOT NULL,
    note TEXT,
    created_by BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS prompt_versions_kind_idx ON prompt_versions(kind, created_at DESC);

-- Что действует сейчас. Нет строки для (kind, 0) — исходный промт из кода.
-- Нет личной строки — человек получает общую версию.
CREATE TABLE IF NOT EXISTS prompt_active (
    kind TEXT NOT NULL,
    owner_tg BIGINT NOT NULL DEFAULT 0,
    version_id BIGINT NOT NULL REFERENCES prompt_versions(id),
    updated_by BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, owner_tg)
);

-- Открытая правка: одна на человека, удаляется при выходе из режима.
CREATE TABLE IF NOT EXISTS prompt_drafts (
    telegram_user_id BIGINT PRIMARY KEY,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    start_body TEXT NOT NULL,
    note TEXT,
    history JSONB NOT NULL DEFAULT '[]'::jsonb,
    preview_ok BOOLEAN,
    previewed_body TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
