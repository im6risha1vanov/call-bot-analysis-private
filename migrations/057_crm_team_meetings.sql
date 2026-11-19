CREATE TABLE IF NOT EXISTS crm_team_meetings (
 id BIGSERIAL PRIMARY KEY, company_id BIGINT NOT NULL, series_id TEXT NOT NULL,
 source TEXT NOT NULL, source_session_id TEXT NOT NULL, owner_ids BIGINT[] NOT NULL,
 revision TEXT NOT NULL, source_updated_at TIMESTAMPTZ NOT NULL,
 state JSONB NOT NULL, transcript JSONB, report JSONB,
 protocol JSONB NOT NULL DEFAULT '{}'::jsonb, protocol_version INTEGER NOT NULL DEFAULT 1,
 imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (source, source_session_id)
);
CREATE INDEX IF NOT EXISTS crm_team_meetings_company_series_idx ON crm_team_meetings(company_id, series_id, source_updated_at DESC);
CREATE TABLE IF NOT EXISTS crm_team_meeting_conversions (
 meeting_id BIGINT NOT NULL REFERENCES crm_team_meetings(id), item_id BIGINT NOT NULL,
 assignment_id BIGINT NOT NULL, request_fingerprint TEXT NOT NULL, PRIMARY KEY(meeting_id,item_id)
);
