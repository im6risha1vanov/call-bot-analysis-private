-- Additive: no historical analyses, messages or experiment rows are modified.
CREATE TABLE IF NOT EXISTS call_report_runs (
 id BIGSERIAL PRIMARY KEY,
 call_id BIGINT NOT NULL REFERENCES calls(id),
 client_id INTEGER NOT NULL REFERENCES clients(id),
 source_key TEXT NOT NULL, extension TEXT,
 transcript_sha256 TEXT NOT NULL, transcript TEXT NOT NULL,
 metadata JSONB NOT NULL, primary_analysis JSONB NOT NULL,
 configuration JSONB NOT NULL, auxiliary JSONB,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready')),
 summary TEXT, expanded TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), ready_at TIMESTAMPTZ,
 UNIQUE(client_id,call_id,source_key),
 UNIQUE(id,call_id)
);
CREATE INDEX IF NOT EXISTS call_report_runs_client_call_idx ON call_report_runs(client_id,call_id,id DESC);
CREATE TABLE IF NOT EXISTS call_report_delivery (
 run_id BIGINT NOT NULL, call_id BIGINT NOT NULL,
 recipient BIGINT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','uncertain','forbidden')),
 message_id BIGINT, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(call_id,run_id,recipient),
 FOREIGN KEY(run_id,call_id) REFERENCES call_report_runs(id,call_id)
);
CREATE INDEX IF NOT EXISTS call_report_delivery_status_idx ON call_report_delivery(status,updated_at);
