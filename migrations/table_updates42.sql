-- Discord is authoritative when a whole Goyangi set duplicates a Discord post.
BEGIN;
CREATE TABLE IF NOT EXISTS goyangi_duplicate_sets (
    set_id TEXT PRIMARY KEY,
    discord_root_id TEXT NOT NULL,
    evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS goyangi_duplicate_archive (
    content_link_id BIGINT PRIMARY KEY,
    set_id TEXT NOT NULL,
    row_data JSONB NOT NULL,
    removed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Hold sets that still need media verification, including across cursor advances.
CREATE TABLE IF NOT EXISTS goyangi_pending_sets (
    set_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    retry_after TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason TEXT NOT NULL
);
INSERT INTO goyangi_pending_sets(set_id,payload,reason)
    SELECT DISTINCT goyangi_set_id,
           jsonb_build_object('id',goyangi_set_id,'existing_only',true), 'existing set recheck'
    FROM content_links
    WHERE source_kind = 'goyangi' AND goyangi_set_id IS NOT NULL
    ON CONFLICT(set_id) DO NOTHING;
CREATE TABLE IF NOT EXISTS content_fingerprints (
    url TEXT PRIMARY KEY,
    fingerprint JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS content_links_goyangi_set_idx
    ON content_links(goyangi_set_id) WHERE source_kind = 'goyangi';
-- Also protects against a still-running older ingestion worker and backfills.
CREATE OR REPLACE FUNCTION reject_duplicate_goyangi_set() RETURNS trigger AS $$
BEGIN
    IF NEW.source_kind = 'goyangi' THEN
        PERFORM pg_advisory_xact_lock(hashtext('hanni:cross-source-dedup'));
        IF EXISTS (SELECT 1 FROM goyangi_duplicate_sets WHERE set_id = NEW.goyangi_set_id) THEN
            RETURN NULL;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS content_links_reject_duplicate_goyangi ON content_links;
CREATE TRIGGER content_links_reject_duplicate_goyangi
    BEFORE INSERT OR UPDATE ON content_links
    FOR EACH ROW EXECUTE FUNCTION reject_duplicate_goyangi_set();
COMMIT;
