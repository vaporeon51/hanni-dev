-- Tier 0 exact dedup: persist Goyangi mirror URLs and cache Imgur album expansions.
-- Rerunnable. Mirror lets imgur-origin clips match without downloads/ffmpeg.
-- Album cache eliminates repeat Imgur API calls across sets and runs.
BEGIN;
ALTER TABLE content_links
    ADD COLUMN IF NOT EXISTS mirror_url TEXT;
CREATE TABLE IF NOT EXISTS imgur_album_cache (
    album_id TEXT PRIMARY KEY,
    assets JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS content_links_role_uploaded_idx
    ON content_links (role_id, uploaded_date)
    WHERE source_message_id IS NOT NULL;
COMMIT;
