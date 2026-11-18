CREATE TABLE IF NOT EXISTS robot_reply_auto_commitments (
 project_id BIGINT NOT NULL REFERENCES robot_reply_projects(id) ON DELETE CASCADE,
 phone TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('agreed','withdrawn')),
 callback_at TIMESTAMPTZ, source_call_id TEXT NOT NULL, source_at TIMESTAMPTZ NOT NULL,
 evidence JSONB NOT NULL DEFAULT '{}'::jsonb, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK((state='agreed' AND callback_at IS NOT NULL) OR (state='withdrawn' AND callback_at IS NULL)),
 PRIMARY KEY(project_id,phone)
);
CREATE TABLE IF NOT EXISTS robot_reply_commitment_scans (
 call_id TEXT PRIMARY KEY, scanned_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
