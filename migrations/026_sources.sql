-- Подборки доноров со страницы «Источники» (crm/sources.py, static/sources.js).
-- Донор — компания, номер или сайт которой задают в проекте на платформе данных.
-- Черновик подборки живёт в браузере менеджера; сюда попадает то, что сохранили кнопкой.
CREATE TABLE IF NOT EXISTS source_selections (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    niche TEXT NOT NULL,  -- id ниши из справочника sources-data.js или custom
    niche_name TEXT NOT NULL,
    query TEXT,
    city TEXT,
    project_client TEXT,  -- клиент, под которого подбирали (чтобы не взять его самого донором)
    seg TEXT,  -- '' | eco | prem
    donors JSONB NOT NULL,
    author_tg BIGINT,
    author_ext TEXT,
    author_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS source_selections_client_idx ON source_selections(client_id, updated_at DESC) WHERE deleted_at IS NULL;
