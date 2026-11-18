CREATE TABLE IF NOT EXISTS robot_reply_projects (
 id BIGSERIAL PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, operator TEXT,
 profile TEXT NOT NULL DEFAULT 'generic' CHECK(profile IN ('generic','packaging')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS robot_reply_project_campaigns (
 project_id BIGINT NOT NULL REFERENCES robot_reply_projects(id), campaign_id BIGINT PRIMARY KEY REFERENCES robot_campaigns(id)
);
CREATE TABLE IF NOT EXISTS robot_reply_contact_memory (
 project_id BIGINT NOT NULL REFERENCES robot_reply_projects(id), phone TEXT NOT NULL,
 callback_at TIMESTAMPTZ NOT NULL, verified_by TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(project_id,phone)
);
CREATE TABLE IF NOT EXISTS robot_reply_candidates (
 id BIGSERIAL PRIMARY KEY, owner TEXT NOT NULL, project_id BIGINT NOT NULL REFERENCES robot_reply_projects(id),
 current_version INTEGER NOT NULL DEFAULT 1, active_version INTEGER, status TEXT NOT NULL DEFAULT 'draft',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS robot_reply_scope ON robot_reply_candidates(owner,project_id);
CREATE TABLE IF NOT EXISTS robot_reply_versions (
 candidate_id BIGINT NOT NULL REFERENCES robot_reply_candidates(id), version INTEGER NOT NULL,
 question TEXT NOT NULL, answer TEXT NOT NULL, audio_pcm BYTEA, sample_rate INTEGER, voice_signature TEXT,
 context_terms JSONB NOT NULL DEFAULT '[]', contact_terms JSONB NOT NULL DEFAULT '[]',
 triggers JSONB NOT NULL DEFAULT '[]', next_action TEXT NOT NULL DEFAULT 'reply' CHECK(next_action IN ('reply')),
 fact_needed BOOLEAN NOT NULL DEFAULT false, fact_note TEXT NOT NULL DEFAULT '',
 previewed_by TEXT, previewed_at TIMESTAMPTZ, approved_by TEXT, approved_at TIMESTAMPTZ,
 rejection_reason TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(candidate_id,version)
);
CREATE TABLE IF NOT EXISTS robot_reply_audit (
 id BIGSERIAL PRIMARY KEY, candidate_id BIGINT NOT NULL REFERENCES robot_reply_candidates(id), version INTEGER NOT NULL,
 actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE robot_reply_candidates ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'manual' CHECK(source_kind IN ('manual','guide','learned'));
