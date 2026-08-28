-- Separate visitor-reported dead links from wrong-idol moderation reports.
-- Wrong-idol reports continue to use content_links.num_reports.
ALTER TABLE content_links
    ADD COLUMN IF NOT EXISTS dead_link_reports INTEGER NOT NULL DEFAULT 0;
