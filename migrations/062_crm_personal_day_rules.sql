CREATE TABLE IF NOT EXISTS crm_personal_day_rule_runs (
 id BIGSERIAL PRIMARY KEY,
 company_id INTEGER NOT NULL REFERENCES clients(id),
 owner_id BIGINT NOT NULL REFERENCES crm_personal_profiles(id),
 telegram_user_id BIGINT NOT NULL,
 local_date DATE NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('morning','evening')),
 timezone TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'recorded' CHECK (status = 'recorded'),
 snapshot JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(company_id, telegram_user_id, local_date, kind)
);
CREATE INDEX IF NOT EXISTS crm_personal_day_rule_runs_owner_idx
 ON crm_personal_day_rule_runs(company_id, telegram_user_id, local_date DESC);
