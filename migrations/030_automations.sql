-- Конструктор автоматизаций (crm/automations.py): сценарии «событие → условия → действия».
-- Всё с client_id (компания), чтобы конструктор без переделок работал в многоклиентной CRM.
-- Миграция только добавляет; повторный запуск ничего не ломает.

-- Подключённые аккаунты: чат Telegram, Google-таблица. Секреты — только в data_enc (Fernet, ENCRYPTION_KEY).
CREATE TABLE IF NOT EXISTS automation_accounts (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id),
 kind TEXT NOT NULL,                    -- telegram, sheets
 title TEXT NOT NULL DEFAULT '',        -- как показывать: «Группа Продажи», «Таблица Оплаты / Лист1»
 data_enc TEXT,                         -- зашифрованный JSON: chat_id, spreadsheet_id и т.п.
 status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'broken')),
 connect_code TEXT UNIQUE,              -- одноразовый код для ссылки на бота (Telegram)
 error TEXT,
 created_by TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS automation_accounts_client_idx ON automation_accounts(client_id, kind);

CREATE TABLE IF NOT EXISTS automations (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id),
 name TEXT NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT false,  -- включает только человек, после пробного запуска
 spec JSONB NOT NULL,                     -- {"trigger": {...}, "if": [...], "actions": [...]}
 hook_token TEXT UNIQUE,                  -- для входящего вебхука /hook/<token>
 last_fired TEXT,                         -- для расписания: день, когда уже сработало
 paused_reason TEXT,                      -- движок выключил сам (слишком часто, лимит)
 created_by TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS automations_client_idx ON automations(client_id, enabled);

-- Очередь событий. Строки CRM пишет триггер базы (ниже), вебхуки и расписание — сам движок.
CREATE TABLE IF NOT EXISTS automation_events (
 id BIGSERIAL PRIMARY KEY,
 client_id INTEGER NOT NULL REFERENCES clients(id),
 kind TEXT NOT NULL,                     -- deal.paid, lead.added, call.outcome, call.reviewed, webhook.in, schedule.daily
 ref_id BIGINT,                          -- строка-источник (deal_events.id, crm_leads.id, ...)
 automation_id BIGINT,                   -- для вебхука и расписания: чей это запуск
 payload JSONB,
 processed_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS automation_events_todo_idx ON automation_events(id) WHERE processed_at IS NULL;

-- Каждый запуск сценария: что пришло, что сделали, ошибки. По нему агент отвечает «почему не пришло».
CREATE TABLE IF NOT EXISTS automation_runs (
 id BIGSERIAL PRIMARY KEY,
 automation_id BIGINT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
 client_id INTEGER NOT NULL REFERENCES clients(id),
 event_id BIGINT,
 test BOOLEAN NOT NULL DEFAULT false,
 status TEXT NOT NULL,                   -- ok, failed, retry, skipped, limit
 payload JSONB,                          -- данные события: повтор идёт с ними же
 steps JSONB NOT NULL DEFAULT '[]',      -- по действию: {type, ok, error, preview}
 attempts INTEGER NOT NULL DEFAULT 1,
 retry_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS automation_runs_auto_idx ON automation_runs(automation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS automation_runs_retry_idx ON automation_runs(retry_at) WHERE status = 'retry';

-- Счётчики под отдельный тариф: запуски за месяц. Лимиты на компанию (пусто = без лимита).
CREATE TABLE IF NOT EXISTS automation_usage (
 client_id INTEGER NOT NULL REFERENCES clients(id),
 month TEXT NOT NULL,                    -- 2026-10
 runs INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY (client_id, month)
);
CREATE TABLE IF NOT EXISTS automation_limits (
 client_id INTEGER PRIMARY KEY REFERENCES clients(id),
 max_active INTEGER,
 max_runs_month INTEGER
);

-- События CRM в очередь: базой, а не в каждом месте кода, которое пишет сделки и звонки.
-- Пока у компании нет включённых сценариев, ничего не пишется.
CREATE OR REPLACE FUNCTION automation_emit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
 kind TEXT;
 ref BIGINT;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM automations WHERE client_id = NEW.client_id AND enabled) THEN
  RETURN NEW;
 END IF;
 -- у таблиц разные столбцы: обращаемся к NEW.<поле> только в своей ветке
 IF TG_TABLE_NAME = 'deal_events' THEN
  kind := 'deal.' || NEW.kind;
  ref := NEW.id;
 ELSIF TG_TABLE_NAME = 'crm_leads' THEN
  kind := 'lead.added';
  ref := NEW.id;
 ELSIF TG_TABLE_NAME = 'crm_outcomes' THEN
  kind := 'call.outcome';
  ref := NEW.id;
 ELSE
  kind := 'call.reviewed';
  ref := NEW.call_id;
 END IF;
 INSERT INTO automation_events(client_id, kind, ref_id) VALUES (NEW.client_id, kind, ref);
 RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS automation_deal_events ON deal_events;
CREATE TRIGGER automation_deal_events AFTER INSERT ON deal_events FOR EACH ROW EXECUTE FUNCTION automation_emit();
DROP TRIGGER IF EXISTS automation_crm_leads ON crm_leads;
CREATE TRIGGER automation_crm_leads AFTER INSERT ON crm_leads FOR EACH ROW EXECUTE FUNCTION automation_emit();
DROP TRIGGER IF EXISTS automation_crm_outcomes ON crm_outcomes;
CREATE TRIGGER automation_crm_outcomes AFTER INSERT ON crm_outcomes FOR EACH ROW EXECUTE FUNCTION automation_emit();
DROP TRIGGER IF EXISTS automation_call_reviews ON call_reviews;
CREATE TRIGGER automation_call_reviews AFTER INSERT ON call_reviews FOR EACH ROW EXECUTE FUNCTION automation_emit();
