CREATE TABLE IF NOT EXISTS crm_team_assignments (
 id BIGSERIAL PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES clients(id),
 creator_id BIGINT NOT NULL, assignee_id BIGINT NOT NULL,
 title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', due_at TIMESTAMPTZ, deal_id TEXT,
 status TEXT NOT NULL DEFAULT 'assigned' CHECK(status IN ('assigned','in_progress','review','accepted','cancelled')),
 blocker TEXT NOT NULL DEFAULT '', review_note TEXT NOT NULL DEFAULT '', result_text TEXT NOT NULL DEFAULT '', result_links JSONB NOT NULL DEFAULT '[]',
 source_meeting_id BIGINT, source_item_id BIGINT,
 version INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(id, company_id)
);
CREATE INDEX IF NOT EXISTS crm_team_assignments_company_people_idx ON crm_team_assignments(company_id, creator_id, assignee_id);
CREATE UNIQUE INDEX IF NOT EXISTS crm_team_assignments_source_idx ON crm_team_assignments(company_id, source_meeting_id, source_item_id) WHERE source_meeting_id IS NOT NULL AND source_item_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS crm_team_assignment_history (
 assignment_id BIGINT NOT NULL, company_id INTEGER NOT NULL, version INTEGER NOT NULL,
 actor_id BIGINT NOT NULL, action TEXT NOT NULL, snapshot JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(assignment_id, version), FOREIGN KEY(assignment_id, company_id) REFERENCES crm_team_assignments(id, company_id)
);
CREATE TABLE IF NOT EXISTS crm_team_assignment_requests (
 company_id INTEGER NOT NULL, actor_id BIGINT NOT NULL, request_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, assignment_id BIGINT NOT NULL, response JSONB NOT NULL,
 PRIMARY KEY(company_id, actor_id, request_key), FOREIGN KEY(assignment_id, company_id) REFERENCES crm_team_assignments(id, company_id)
);
