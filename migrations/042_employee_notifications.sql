ALTER TABLE employees ADD COLUMN IF NOT EXISTS sales_reports_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS technical_reports_enabled boolean NOT NULL DEFAULT true;
