CREATE TABLE IF NOT EXISTS crm_personal_profiles (
 id BIGSERIAL PRIMARY KEY, telegram_user_id BIGINT NOT NULL UNIQUE,
 display_name TEXT NOT NULL, timezone TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS crm_personal_projects (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL REFERENCES crm_personal_profiles(id),
 title TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(id, owner_id)
);
CREATE TABLE IF NOT EXISTS crm_personal_tasks (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL REFERENCES crm_personal_profiles(id),
 project_id BIGINT, title TEXT NOT NULL, expected_result TEXT NOT NULL DEFAULT '',
 result_note TEXT NOT NULL DEFAULT '', blocker TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','in_progress','done','cancelled')),
 due_at TIMESTAMPTZ, planned_day DATE, reminder_at TIMESTAMPTZ,
 version INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(project_id, owner_id) REFERENCES crm_personal_projects(id, owner_id)
);
CREATE TABLE IF NOT EXISTS crm_personal_task_history (
 id BIGSERIAL PRIMARY KEY, task_id BIGINT NOT NULL REFERENCES crm_personal_tasks(id),
 owner_id BIGINT NOT NULL REFERENCES crm_personal_profiles(id), version INTEGER NOT NULL,
 snapshot JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(task_id, version)
);
CREATE INDEX IF NOT EXISTS crm_personal_tasks_owner_idx ON crm_personal_tasks(owner_id, id);
