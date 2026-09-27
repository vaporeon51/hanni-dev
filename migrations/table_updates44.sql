-- Suppress individually confirmed duplicate Goyangi clips without removing
-- the unique members of their containing sets.
BEGIN;
CREATE TABLE IF NOT EXISTS goyangi_duplicate_contents (
    content_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    discord_root_id TEXT NOT NULL,
    evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (content_id, role_id)
);

CREATE OR REPLACE FUNCTION reject_duplicate_goyangi_set() RETURNS trigger AS $$
BEGIN
    IF NEW.source_kind = 'goyangi' THEN
        PERFORM pg_advisory_xact_lock(hashtext('hanni:cross-source-dedup'));
        IF EXISTS (SELECT 1 FROM goyangi_duplicate_sets WHERE set_id = NEW.goyangi_set_id)
           OR EXISTS (
               SELECT 1 FROM goyangi_duplicate_contents
               WHERE content_id = NEW.goyangi_content_id AND role_id = NEW.role_id
           ) THEN
            RETURN NULL;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS content_links_reject_duplicate_goyangi ON content_links;
CREATE TRIGGER content_links_reject_duplicate_goyangi
    BEFORE INSERT ON content_links
    FOR EACH ROW EXECUTE FUNCTION reject_duplicate_goyangi_set();
COMMIT;
