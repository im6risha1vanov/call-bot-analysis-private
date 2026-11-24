-- Клиент презентации без звонка в Mango (review/presentations.py, crm/deals.py).
-- client_contact — что написано в колонке «Телефон клиента» реестра (номер или ник);
-- phone — номер клиента цифрами: из реестра или указан вручную в CRM («Привязать к клиенту»);
-- по нему презентация попадает в сделку, даже если звонка с этим номером в Mango нет.
ALTER TABLE presentations ADD COLUMN IF NOT EXISTS client_contact TEXT;
ALTER TABLE presentations ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE presentations ADD COLUMN IF NOT EXISTS linked_by TEXT;  -- кто привязал вручную
ALTER TABLE presentations ADD COLUMN IF NOT EXISTS linked_at TIMESTAMPTZ;
