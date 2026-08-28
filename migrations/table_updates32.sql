-- Discord's article embed is a confirmed dead-link result. Backfill URLs that
-- were waiting for a second confirmation under the previous web-worker rule.
WITH confirmed_urls AS (
    SELECT DISTINCT url
    FROM content_links
    WHERE last_check_status = 'dead'
      AND dead_check_failures > 0
)
UPDATE content_links AS cl
SET is_dead = TRUE,
    is_recovery_exhausted = COALESCE(cl.recovery_generation, 0) >= 3
FROM confirmed_urls
WHERE cl.url = confirmed_urls.url
  AND cl.is_dead = FALSE;
