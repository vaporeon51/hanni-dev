-- Store coarse, first-party session totals without retaining IP addresses.
CREATE TABLE IF NOT EXISTS web_analytics_daily (
    analytics_date DATE NOT NULL,
    country_code VARCHAR(2) NOT NULL,
    session_count BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (analytics_date, country_code),
    CHECK (country_code ~ '^[A-Z]{2}$'),
    CHECK (session_count >= 0)
);
