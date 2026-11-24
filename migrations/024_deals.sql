-- Сделки в CRM (crm/deals.py). Сделка — клиент по номеру телефона (10 последних
-- цифр); этапы до презентации CRM ставит сама по звонкам и разборам, остальное —
-- события, которые отмечают люди: материалы в Telegram или на почту, договор,
-- правки, подпись, оплата, отказ с причиной. События не удаляются: отмена — новое
-- событие reopen, так видно, кто и когда что поменял.
CREATE TABLE IF NOT EXISTS deal_events (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    phone TEXT NOT NULL,
    kind TEXT NOT NULL,  -- presentation, materials, contract_sent, contract_edits, contract_signed, paid, lost, reopen, note
    reason TEXT,  -- причина отказа (код из crm/deals.py LOST_REASONS)
    note TEXT,
    amount NUMERIC(14,2),
    extension TEXT,  -- чья сделка на момент события
    author_tg BIGINT,
    author_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS deal_events_phone_idx ON deal_events(client_id, phone, created_at);
