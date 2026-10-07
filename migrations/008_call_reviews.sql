-- Outcome-based call review (prompt call_review_v2). Additive: old analyses,
-- runs and deliveries are not modified. One review per call.
CREATE TABLE IF NOT EXISTS call_reviews (
 call_id BIGINT PRIMARY KEY REFERENCES calls(id),
 client_id INTEGER NOT NULL REFERENCES clients(id),
 extension TEXT,
 prompt_version TEXT NOT NULL,
 kind TEXT, success BOOLEAN, chance TEXT, category TEXT,
 data JSONB NOT NULL,
 cost_units NUMERIC(14,4),
 manager_sent_at TIMESTAMPTZ, rop_sent_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS call_reviews_client_created_idx ON call_reviews(client_id,created_at);
