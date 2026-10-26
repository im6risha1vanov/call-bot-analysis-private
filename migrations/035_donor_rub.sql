-- Агент доноров: расход в рублях (ИИ по курсу ЦБ, поиск Яндекса по прайсу в рублях). Колонки usd больше не пишутся.
ALTER TABLE donor_runs ADD COLUMN IF NOT EXISTS rub NUMERIC(12,2) NOT NULL DEFAULT 0;
ALTER TABLE donor_spend ADD COLUMN IF NOT EXISTS rub NUMERIC(12,2) NOT NULL DEFAULT 0;
-- Модель для разбора сайтов в прогоне: '' — основная (dreamn), иначе модель Яндекса (сравнение цены и качества).
ALTER TABLE donor_runs ADD COLUMN IF NOT EXISTS sites_model TEXT NOT NULL DEFAULT '';
