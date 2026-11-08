ALTER TABLE automation_events ADD COLUMN IF NOT EXISTS webhook_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS automation_events_webhook_key_idx
    ON automation_events(client_id, automation_id, webhook_key)
    WHERE webhook_key IS NOT NULL;
