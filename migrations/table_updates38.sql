-- Drop the retired personal-ELO tables.
--
-- visitor_elo / web_visitors backed per-visitor ratings, removed when the
-- Mine tab was repointed at client-side sorter replays: nothing in the web
-- app or the bot reads or writes them (12 + 5 orphan rows). Child first:
-- visitor_elo holds the FK to web_visitors.

DROP TABLE IF EXISTS visitor_elo;
DROP TABLE IF EXISTS web_visitors;
