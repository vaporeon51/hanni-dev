-- One live idol Elo score; original raw ratings remain in role_info.
-- Draft migration: apply before sync_sorter_idols.py --apply and deployment.
BEGIN;
CREATE TABLE IF NOT EXISTS idol_ratings (
    role_id TEXT PRIMARY KEY,
    member_name TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    image_url TEXT,
    global_elo NUMERIC(20,10) NOT NULL DEFAULT 1200,
    global_win_count INTEGER NOT NULL DEFAULT 0,
    global_match_count INTEGER NOT NULL DEFAULT 0
);
-- Start from the previous displayed score, so there is no confidence
-- transition or second score that can move against the actual result.
INSERT INTO idol_ratings (role_id, member_name, group_name, image_url,
                         global_elo, global_win_count, global_match_count)
SELECT role_id, COALESCE(member_name, ''), COALESCE(group_name, ''), image_url,
       ROUND(1200.0 + (global_elo - 1200)::numeric * global_match_count
             / (global_match_count + 15)),
       global_win_count, global_match_count FROM role_info
ON CONFLICT (role_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS visitor_idol_budget (
    visitor_token TEXT NOT NULL, -- SHA-256 digest, never a raw cookie for new votes
    day DATE NOT NULL,
    role_id TEXT NOT NULL REFERENCES idol_ratings(role_id) ON DELETE CASCADE,
    spent NUMERIC(20,10) NOT NULL DEFAULT 0 CHECK (spent BETWEEN 0 AND 12),
    PRIMARY KEY (visitor_token, day, role_id)
);
-- Reuse visitor_pair_votes from migration 37 with hashed visitor keys.
-- Its old raw-cookie rows expire through the same short-retention cleanup.

CREATE TABLE IF NOT EXISTS idol_leaderboard_snapshots (
    snapshot_date DATE NOT NULL,
    role_id TEXT NOT NULL REFERENCES idol_ratings(role_id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    elo INTEGER NOT NULL,
    vote_count INTEGER NOT NULL DEFAULT 0,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, role_id),
    UNIQUE (snapshot_date, rank)
);
COMMIT;
