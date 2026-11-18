-- Источники обучения отделены от одобренных ответов библиотеки.
CREATE TABLE IF NOT EXISTS robot_reply_learning_sources (
 owner TEXT NOT NULL, project_id BIGINT NOT NULL REFERENCES robot_reply_projects(id) ON DELETE CASCADE,
 source_type TEXT NOT NULL CHECK(source_type IN ('robot','human')), source_id TEXT NOT NULL,
 transcript_hash TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT '',
 metadata JSONB NOT NULL DEFAULT '{}', evidence JSONB NOT NULL DEFAULT '[]', updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(owner,project_id,source_type,source_id)
);
-- Только подтверждённая человеком связь сотрудника/периода с конкретным проектом.
CREATE TABLE IF NOT EXISTS robot_reply_learning_bindings (
 owner TEXT NOT NULL, project_id BIGINT NOT NULL REFERENCES robot_reply_projects(id) ON DELETE CASCADE,
 client_id INTEGER NOT NULL, extension TEXT NOT NULL, from_at TIMESTAMPTZ NOT NULL, to_at TIMESTAMPTZ NOT NULL,
 verified_by TEXT NOT NULL CHECK(length(btrim(verified_by))>0), CHECK(to_at>from_at),
 PRIMARY KEY(owner,project_id,client_id,extension,from_at,to_at)
);

CREATE TABLE IF NOT EXISTS robot_reply_learning_links (
 candidate_id BIGINT NOT NULL REFERENCES robot_reply_candidates(id) ON DELETE CASCADE,
 owner TEXT NOT NULL, project_id BIGINT NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
 FOREIGN KEY(owner,project_id,source_type,source_id) REFERENCES robot_reply_learning_sources(owner,project_id,source_type,source_id) ON DELETE CASCADE,
 PRIMARY KEY(candidate_id,owner,project_id,source_type,source_id)
);

CREATE TABLE IF NOT EXISTS robot_reply_learning_scripts (
 owner TEXT NOT NULL, project_id BIGINT NOT NULL REFERENCES robot_reply_projects(id) ON DELETE CASCADE,
 client_id INTEGER NOT NULL, script TEXT NOT NULL, verified_by TEXT NOT NULL CHECK(length(btrim(verified_by))>0),
 PRIMARY KEY(owner,project_id,client_id,script)
);

ALTER TABLE robot_reply_learning_sources ADD COLUMN IF NOT EXISTS checked_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS robot_reply_learning_clusters (
 owner TEXT NOT NULL, project_id BIGINT NOT NULL REFERENCES robot_reply_projects(id) ON DELETE CASCADE,
 intent TEXT NOT NULL, candidate_id BIGINT NOT NULL REFERENCES robot_reply_candidates(id),
 fingerprint TEXT NOT NULL, generated_version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(owner,project_id,intent)
);

ALTER TABLE robot_reply_learning_links ADD COLUMN IF NOT EXISTS manual BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS robot_reply_learning_refresh (
 owner TEXT NOT NULL, project_id BIGINT NOT NULL REFERENCES robot_reply_projects(id) ON DELETE CASCADE,
 checked_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(owner,project_id)
);

CREATE INDEX IF NOT EXISTS robot_reply_learning_sources_changed ON robot_reply_learning_sources(owner,project_id,updated_at);
