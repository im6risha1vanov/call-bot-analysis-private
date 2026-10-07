-- Additive policy only. No historical scores, sessions or clients are rewritten.
CREATE TABLE IF NOT EXISTS call_analysis_policy (
 client_id BIGINT PRIMARY KEY REFERENCES clients(id),
 active TEXT NOT NULL CHECK(active IN ('saved_current','legacy')),
 saved_configuration JSONB NOT NULL,
 backup_path TEXT NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
