CREATE TABLE IF NOT EXISTS crm_personal_day_settings (
 owner_id BIGINT PRIMARY KEY REFERENCES crm_personal_profiles(id),
 work_start TIME NOT NULL DEFAULT '09:00', work_end TIME NOT NULL DEFAULT '18:00',
 priorities JSONB NOT NULL DEFAULT '[]', morning_time TIME, evening_time TIME,
 version INTEGER NOT NULL DEFAULT 1, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK (work_start < work_end)
);
