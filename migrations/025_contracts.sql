-- Журнал договоров из генератора в CRM (crm/contracts.py). Договор собирается в браузере
-- из шаблона, сервер только выдаёт номер и записывает, кто, кому и что оформил.
-- Номера сквозные, с 1, общие для ОПС, ГЦК и КЦ; у допсоглашения номера нет.
CREATE TABLE IF NOT EXISTS contracts (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    num INTEGER,  -- NULL у допсоглашения
    type TEXT NOT NULL,  -- ops, gck, kc, ds
    contract_date DATE,
    company TEXT,
    inn TEXT,
    amount NUMERIC(14,2),
    tariff TEXT,
    base_num TEXT,  -- номер договора ГЦК, к которому допсоглашение
    phone TEXT,  -- сделка (10 последних цифр номера), если договор оформлен из карточки сделки
    extension TEXT,  -- кто оформил
    author_tg BIGINT,
    author_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS contracts_num_idx ON contracts(client_id, num) WHERE num IS NOT NULL;
CREATE INDEX IF NOT EXISTS contracts_phone_idx ON contracts(client_id, phone);
