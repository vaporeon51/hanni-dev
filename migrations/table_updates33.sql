-- Speed exact-root and legacy posting-burst collection lookups.
CREATE INDEX IF NOT EXISTS content_links_role_root_message
    ON content_links (role_id, root_message_id)
    WHERE root_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS content_links_legacy_collection_timeline
    ON content_links (role_id, author_id, uploaded_date, content_link_id)
    WHERE root_message_id IS NULL
      AND author_id IS NOT NULL
      AND uploaded_date IS NOT NULL;
