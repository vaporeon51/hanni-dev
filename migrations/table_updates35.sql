-- Store coarse usage totals for the plain-text random-link API.
CREATE TABLE IF NOT EXISTS api_link_analytics_daily (
    analytics_date DATE PRIMARY KEY,
    request_count BIGINT NOT NULL DEFAULT 0,
    success_count BIGINT NOT NULL DEFAULT 0,
    no_result_count BIGINT NOT NULL DEFAULT 0,
    cycle_reset_count BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (request_count >= 0),
    CHECK (success_count >= 0),
    CHECK (no_result_count >= 0),
    CHECK (cycle_reset_count >= 0),
    CHECK (success_count + no_result_count = request_count)
);
