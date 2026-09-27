-- The suppression guard only needs to serialize Goyangi inserts. Taking the
-- global advisory lock from UPDATE triggers can invert lock order with cleanup
-- (which takes the advisory lock before deleting the row).
BEGIN;
DROP TRIGGER IF EXISTS content_links_reject_duplicate_goyangi ON content_links;
CREATE TRIGGER content_links_reject_duplicate_goyangi
    BEFORE INSERT ON content_links
    FOR EACH ROW EXECUTE FUNCTION reject_duplicate_goyangi_set();
COMMIT;
