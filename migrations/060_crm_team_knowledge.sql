CREATE TABLE IF NOT EXISTS crm_team_knowledge_sources (
 id BIGSERIAL PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES clients(id), owner_id BIGINT NOT NULL,
 title TEXT NOT NULL, provenance_url TEXT, provenance_check TEXT NOT NULL DEFAULT '',
 source_date DATE, scope TEXT NOT NULL CHECK (scope IN ('private','shared')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (id, company_id)
);
CREATE INDEX IF NOT EXISTS crm_team_knowledge_sources_access_idx ON crm_team_knowledge_sources(company_id,owner_id,scope);
CREATE TABLE IF NOT EXISTS crm_team_knowledge_documents (
 id BIGSERIAL PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES clients(id), source_id BIGINT NOT NULL,
 owner_id BIGINT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 scope TEXT NOT NULL CHECK (scope IN ('private','shared')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (source_id, company_id) REFERENCES crm_team_knowledge_sources(id, company_id)
);
CREATE INDEX IF NOT EXISTS crm_team_knowledge_documents_access_idx ON crm_team_knowledge_documents(company_id,owner_id,scope);
