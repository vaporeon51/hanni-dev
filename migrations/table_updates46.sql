-- Durable completed-sort receipts: retries and shared links never count twice.
-- Apply before deploying the completed-ballot writer. No rating reset.
CREATE TABLE IF NOT EXISTS sorter_ballots (
    ballot_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
