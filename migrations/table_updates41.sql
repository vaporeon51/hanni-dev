-- Single set-identity expression for the /sets feed: Discord root message
-- or goyangi set, so collection queries group without inline string surgery.
BEGIN;
ALTER TABLE content_links
    ADD COLUMN IF NOT EXISTS set_key TEXT GENERATED ALWAYS AS
        (COALESCE(root_message_id, 'goyangi:' || goyangi_set_id)) STORED;
CREATE INDEX IF NOT EXISTS content_links_role_set_key_idx
    ON content_links (role_id, set_key)
    WHERE set_key IS NOT NULL;
COMMIT;
