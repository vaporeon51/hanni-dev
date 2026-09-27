-- Goyangi second-source ingest: per-content provenance and cursor.
-- Rerunnable; NULL provenance on all pre-existing rows except the viewer-URL
-- backfill below, which seeds content-ID dedup from stored /v/<id> links.
BEGIN;
ALTER TABLE content_links
    ADD COLUMN IF NOT EXISTS goyangi_content_id TEXT,
    ADD COLUMN IF NOT EXISTS goyangi_set_id TEXT;

-- One row per (content, role), mirroring the Discord provenance unique index.
CREATE UNIQUE INDEX IF NOT EXISTS content_links_goyangi_content_role_unique
    ON content_links (goyangi_content_id, role_id)
    WHERE goyangi_content_id IS NOT NULL;

-- Seed provenance on rows the Discord scraper ingested as goyangi viewer
-- links (https://goyangi.pics/v/<content-id>.webp) so the content-ID dedup
-- recognizes them without re-reading Discord history.
UPDATE content_links
SET goyangi_content_id = split_part(substring(url from '/v/([^/?#]+)'), '.', 1)
WHERE goyangi_content_id IS NULL
  AND url LIKE '%goyangi.pics/v/%';

-- Singleton cursor for the goyangi poller; never touches update_log.
CREATE TABLE IF NOT EXISTS goyangi_ingest_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_content_created TIMESTAMPTZ,
    last_content_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO goyangi_ingest_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
COMMIT;
