-- Web-only dead-link checking state.
--
-- The old Discord checker kept an in-memory/URL cursor. The web worker needs
-- per-URL timestamps so a restart or a second dyno does not reset the scan.
ALTER TABLE content_links
    ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_check_status VARCHAR(16) NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS last_check_error TEXT,
    ADD COLUMN IF NOT EXISTS dead_check_failures INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS content_links_dead_check_due_idx
    ON content_links (last_checked_at)
    WHERE is_dead = FALSE;
