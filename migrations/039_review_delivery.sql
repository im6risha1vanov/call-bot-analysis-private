CREATE TABLE IF NOT EXISTS review_delivery (
    call_id BIGINT NOT NULL REFERENCES call_reviews(call_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('manager', 'copy', 'rop')),
    recipient BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'uncertain')),
    retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts INTEGER NOT NULL DEFAULT 0,
    body TEXT,
    error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (call_id, kind, recipient)
);
