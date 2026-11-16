ALTER TABLE crm_personal_tasks ADD COLUMN IF NOT EXISTS reminder_revision BIGINT NOT NULL DEFAULT 1;
ALTER TABLE crm_personal_tasks ADD COLUMN IF NOT EXISTS reminders_enabled BOOLEAN NOT NULL DEFAULT true;
CREATE OR REPLACE FUNCTION crm_personal_reminder_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (coalesce(NEW.reminder_at, NEW.due_at), NEW.reminders_enabled, NEW.reminder_at IS NULL) IS DISTINCT FROM (coalesce(OLD.reminder_at, OLD.due_at), OLD.reminders_enabled, OLD.reminder_at IS NULL)
    OR (NEW.status IS DISTINCT FROM OLD.status AND (NEW.status IN ('done','cancelled') OR OLD.status IN ('done','cancelled'))) THEN
   NEW.reminder_revision := OLD.reminder_revision + 1;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS crm_personal_reminder_revision_trigger ON crm_personal_tasks;
CREATE TRIGGER crm_personal_reminder_revision_trigger BEFORE UPDATE ON crm_personal_tasks
 FOR EACH ROW EXECUTE FUNCTION crm_personal_reminder_revision();
CREATE TABLE IF NOT EXISTS crm_personal_voice_drafts (
 event_key TEXT PRIMARY KEY, telegram_user_id BIGINT NOT NULL, token TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL CHECK(status IN ('processing','preview','saved','cancelled','failed')),
 transcript TEXT, title TEXT, due_at TIMESTAMPTZ, planned_day DATE, reminder_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE crm_personal_voice_drafts ADD COLUMN IF NOT EXISTS planned_day DATE;
CREATE TABLE IF NOT EXISTS crm_personal_reminder_delivery (
 task_id BIGINT NOT NULL REFERENCES crm_personal_tasks(id), revision BIGINT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','uncertain','blocked','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(task_id, revision)
);

CREATE OR REPLACE FUNCTION crm_personal_profile_timezone_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.timezone IS DISTINCT FROM OLD.timezone THEN
   UPDATE crm_personal_tasks SET reminder_revision=reminder_revision+1 WHERE owner_id=NEW.id AND reminder_at IS NULL AND due_at IS NOT NULL;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS crm_personal_profile_timezone_revision_trigger ON crm_personal_profiles;
CREATE TRIGGER crm_personal_profile_timezone_revision_trigger AFTER UPDATE ON crm_personal_profiles
 FOR EACH ROW EXECUTE FUNCTION crm_personal_profile_timezone_revision();
