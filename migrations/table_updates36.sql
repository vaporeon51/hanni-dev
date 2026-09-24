-- Web bias sorter identity + personal ELO.
--
-- The Discord bot keyed personal ELO by Discord snowflake (user_elo.user_id
-- BIGINT). The web app is anonymous, so each browser gets a random
-- `hanni_visitor` cookie token. web_visitors maps that token to a row, and
-- visitor_elo mirrors user_elo with a BIGINT FK so existing ELO math,
-- leaderboards, and weekly snapshots keep working unchanged.
--
-- Discord snowflakes are ~1e17+ while web visitor ids start at 1, so sharing
-- scope_type='personal' / scope_id in bias_leaderboard_snapshots is safe.
-- Small ids = web visitors, huge ids = Discord users.

CREATE TABLE IF NOT EXISTS web_visitors (
    visitor_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    visitor_token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (visitor_token <> '')
);

CREATE TABLE IF NOT EXISTS visitor_elo (
    visitor_id BIGINT NOT NULL REFERENCES web_visitors(visitor_id) ON DELETE CASCADE,
    role_id VARCHAR NOT NULL REFERENCES role_info(role_id) ON DELETE CASCADE,
    personal_elo INTEGER NOT NULL DEFAULT 1200,
    win_count INTEGER NOT NULL DEFAULT 0,
    match_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (visitor_id, role_id),
    CHECK (personal_elo >= 0)
);

CREATE INDEX IF NOT EXISTS visitor_elo_role_idx ON visitor_elo (role_id);
CREATE INDEX IF NOT EXISTS web_visitors_last_seen_idx ON web_visitors (last_seen_at);
