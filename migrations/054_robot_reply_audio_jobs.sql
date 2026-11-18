-- One durable retry record per immutable draft version. A lease is recovered after a crash.
CREATE TABLE IF NOT EXISTS robot_reply_audio_jobs (
 candidate_id BIGINT NOT NULL, version INTEGER NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, next_retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 lease_token TEXT, lease_until TIMESTAMPTZ, last_error TEXT,
 PRIMARY KEY(candidate_id,version),
 FOREIGN KEY(candidate_id,version) REFERENCES robot_reply_versions(candidate_id,version) ON DELETE CASCADE
);
