-- Daily pair ballots (anti-skew).
--
-- One person, one vote per pair per day: a matchup's first meeting each day
-- moves global ELO (fading with daily volume); rematches are logged in
-- role_info win/match counts but move nothing. Marathons fade via volume,
-- farmed pairs die on the second pick — one rule covers both.
-- Keyed by the anonymous `hanni_visitor` cookie token; clearing cookies
-- starts a fresh ballot (accepted tradeoff — stops casual skew, not
-- scripted Sybil). Day boundary is UTC (see register_pair_vote callers).

CREATE TABLE IF NOT EXISTS visitor_pair_votes (
    visitor_token TEXT NOT NULL,
    day DATE NOT NULL DEFAULT CURRENT_DATE,
    pair_key TEXT NOT NULL,
    PRIMARY KEY (visitor_token, day, pair_key),
    CHECK (visitor_token <> ''),
    CHECK (pair_key <> '')
);
