ALTER TABLE crm_outcomes
    ADD COLUMN IF NOT EXISTS callback_task_id BIGINT REFERENCES callback_tasks(id) ON DELETE SET NULL;
