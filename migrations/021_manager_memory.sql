-- Память по менеджерам (review/memory.py): карточка бота о слабых местах, сильных
-- сторонах и фокусе. Одна карточка на менеджера в день; старые не удаляются, это
-- история. stats — цифры, по которым карточка написана (их считает код, не модель).
CREATE TABLE IF NOT EXISTS manager_memory (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    extension TEXT NOT NULL,
    day DATE NOT NULL,
    card JSONB NOT NULL,
    stats JSONB NOT NULL,
    talks INTEGER NOT NULL DEFAULT 0,
    prompt_version TEXT NOT NULL,
    cost_units NUMERIC(14,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, extension, day)
);
CREATE INDEX IF NOT EXISTS manager_memory_ext_idx ON manager_memory(client_id, extension, day DESC);
