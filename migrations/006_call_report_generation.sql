-- Receipts for explicitly requested reanalysis; existing scores remain intact.
CREATE TABLE IF NOT EXISTS call_report_generation (
 run_id BIGINT NOT NULL REFERENCES call_report_runs(id),
 stage TEXT NOT NULL CHECK(stage IN ('score','combined')),
 prompt TEXT NOT NULL, settings JSONB NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending'
   CHECK(status IN ('pending','processing','received','complete','failed','uncertain')),
 response_text TEXT, result JSONB, cost_units NUMERIC NOT NULL DEFAULT 0,
 usage JSONB, error TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,stage)
);
