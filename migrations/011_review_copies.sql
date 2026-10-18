-- Копии всех разборов руководителям из REVIEW_COPY_TO (review/store.py deliver_now).
ALTER TABLE call_reviews ADD COLUMN IF NOT EXISTS copy_sent_at TIMESTAMPTZ;
-- Разборы, сделанные до включения копий, задним числом не рассылаются.
UPDATE call_reviews SET copy_sent_at = now() WHERE copy_sent_at IS NULL;
