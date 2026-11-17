-- Ссылки на подтверждённые исторические примеры; цитаты и выводы читаются из разборов.
CREATE TABLE IF NOT EXISTS callcenter_insight_pins (
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    call_id BIGINT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    script TEXT NOT NULL,
    stage_id TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    CHECK (period_end >= period_start),
    PRIMARY KEY (client_id, call_id, period_start, period_end, script, stage_id, issue_code)
);
