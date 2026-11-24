-- Кабинет голосового робота в CRM (crm/robot_store.py): контакты, стоп-лист, кампании по расписанию,
-- звонки робота и цены для расчёта стоимости. owner: '' — наш кабинет (директор), иначе лист клиента в «Прозвон лидов».
CREATE TABLE IF NOT EXISTS robot_contacts (
 id BIGSERIAL PRIMARY KEY,
 owner TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL,
 name TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (owner, phone)
);
CREATE TABLE IF NOT EXISTS robot_stoplist (
 owner TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY (owner, phone)
);
CREATE TABLE IF NOT EXISTS robot_campaigns (
 id BIGSERIAL PRIMARY KEY,
 owner TEXT NOT NULL DEFAULT '',
 name TEXT NOT NULL,
 operator TEXT NOT NULL,
 days SMALLINT[] NOT NULL DEFAULT '{1,2,3,4,5}',  -- 1 понедельник … 7 воскресенье
 start_time TIME NOT NULL DEFAULT '10:00',
 end_time TIME NOT NULL DEFAULT '19:00',
 timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
 attempts SMALLINT NOT NULL DEFAULT 2,
 retry_minutes INTEGER NOT NULL DEFAULT 120,
 status TEXT NOT NULL DEFAULT 'draft',  -- draft | running | paused | done
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS robot_targets (
 id BIGSERIAL PRIMARY KEY,
 campaign_id BIGINT NOT NULL REFERENCES robot_campaigns(id) ON DELETE CASCADE,
 phone TEXT NOT NULL,
 name TEXT NOT NULL DEFAULT '',
 attempts SMALLINT NOT NULL DEFAULT 0,
 last_at TIMESTAMPTZ,
 next_at TIMESTAMPTZ,
 outcome TEXT,
 done BOOLEAN NOT NULL DEFAULT false,
 call_id TEXT,
 UNIQUE (campaign_id, phone)
);
CREATE INDEX IF NOT EXISTS robot_targets_next_idx ON robot_targets(campaign_id, done, next_at);
CREATE TABLE IF NOT EXISTS robot_calls (
 call_id TEXT PRIMARY KEY,
 owner TEXT NOT NULL DEFAULT '',
 campaign_id BIGINT,
 phone TEXT NOT NULL DEFAULT '',
 operator TEXT NOT NULL DEFAULT '',
 at TIMESTAMPTZ NOT NULL,
 outcome TEXT NOT NULL DEFAULT '',
 comment TEXT NOT NULL DEFAULT '',
 duration_s INTEGER NOT NULL DEFAULT 0,
 llm_tokens INTEGER NOT NULL DEFAULT 0,
 tts_chars INTEGER NOT NULL DEFAULT 0,
 recording TEXT,
 data JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS robot_calls_owner_idx ON robot_calls(owner, at DESC);
CREATE TABLE IF NOT EXISTS robot_options (
 key TEXT PRIMARY KEY,
 value JSONB NOT NULL
);
