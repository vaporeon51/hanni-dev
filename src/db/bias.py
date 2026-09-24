"""Web bias-sorter ELO: one global consensus board.

Port of the tsuki-bot ``bias_rater`` logic, minus the Discord guild scope.
Every sorter matchup logs one global vote: the winner gains ELO (K=8) and both
idols gain ``match_count`` (winner also gains ``win_count``).

There is deliberately no per-visitor rating: the personal "Mine" tab renders
the visitor's own sorter ranking (merge-sort output, client-side), so the two
views can never disagree. Ties never touch ELO (caller must skip them).
Identity is only the anonymous ``hanni_visitor`` cookie, used for rate
limiting — no per-visitor rows are written.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass


_KST = datetime.timezone(datetime.timedelta(hours=9))

LEADERBOARD_SNAPSHOT_LIMIT = 45
LEADERBOARD_PAGE_SIZE = 15
GLOBAL_ELO_K = 8

_ACTIVE_IDOL_PREDICATE = (
    "r.member_name IS NOT NULL AND TRIM(r.member_name) != '' "
    "AND r.image_url IS NOT NULL AND TRIM(r.image_url) != ''"
)


def _today_kst() -> datetime.date:
    return datetime.datetime.now(_KST).date()


def _week_start_kst(date: datetime.date | None = None) -> datetime.date:
    if date is None:
        date = _today_kst()
    return date - datetime.timedelta(days=date.weekday())


def calculate_elo_delta(winner_elo: int, loser_elo: int, k: int = 32) -> tuple[int, int]:
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_loser = 1 / (1 + 10 ** ((winner_elo - loser_elo) / 400))
    return round(k * (1 - expected_winner)), round(k * (0 - expected_loser))


@dataclass(frozen=True)
class LeaderboardEntry:
    role_id: str
    member_name: str
    group_name: str
    elo: int
    image_url: str
    previous_rank: int | None = None
    votes: int = 0


@dataclass(frozen=True)
class Leaderboard:
    entries: list[LeaderboardEntry]
    vote_count: int
    movement_baseline_date: datetime.date | None = None


@dataclass(frozen=True)
class GroupLeaderboardEntry:
    group_name: str
    elo: int
    member_count: int
    ranked_member_count: int
    top_members: list[str]
    image_url: str | None
    votes: int = 0
    top_member_images: list[str] | None = None


@dataclass(frozen=True)
class GroupLeaderboard:
    entries: list[GroupLeaderboardEntry]
    vote_count: int
    top_n: int


def format_movement(previous_rank: int | None, rank: int, has_baseline: bool) -> str | None:
    """Discord-style movement token: ▲n / ▼n / – / NEW / None."""
    if not has_baseline:
        return None
    if previous_rank is None:
        return "NEW"
    if previous_rank == rank:
        return "–"
    if previous_rank > rank:
        return f"▲{previous_rank - rank}"
    return f"▼{rank - previous_rank}"


def _build_leaderboard(rows, vote_count: int) -> Leaderboard:
    baseline = next((row[7] for row in rows if len(row) > 7 and row[7] is not None), None)
    return Leaderboard(
        entries=[
            LeaderboardEntry(
                role_id=row[0],
                member_name=row[1],
                group_name=row[2],
                elo=row[3],
                image_url=row[4],
                previous_rank=row[5] if len(row) > 5 else None,
                votes=int(row[6] or 0) if len(row) > 6 else 0,
            )
            for row in rows
        ],
        vote_count=int(vote_count or 0),
        movement_baseline_date=baseline,
    )


def _build_group_leaderboard(rows, vote_count: int, top_n: int) -> GroupLeaderboard:
    return GroupLeaderboard(
        entries=[
            GroupLeaderboardEntry(
                group_name=row[0],
                elo=row[1],
                member_count=row[2],
                ranked_member_count=row[3],
                top_members=list(row[4] or []),
                image_url=row[5],
                votes=int(row[7] or 0) if len(row) > 7 else 0,
                top_member_images=list(row[6] or []) if len(row) > 6 else [],
            )
            for row in rows
        ],
        vote_count=int(vote_count or 0),
        top_n=top_n,
    )


def _pool():
    from src.db import POOL

    return POOL


def record_sorter_vote(winner_id: str, loser_id: str) -> dict[str, int] | None:
    """Record one sorter matchup as a global vote. Returns ELO deltas, or None for bad ids."""
    if not winner_id or not loser_id or winner_id == loser_id:
        return None
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role_id FROM role_info WHERE role_id IN (%s, %s);",
                (winner_id, loser_id),
            )
            found = {row[0] for row in cur.fetchall()}
            if winner_id not in found or loser_id not in found:
                return None

            cur.execute(
                "SELECT role_id, global_elo FROM role_info WHERE role_id IN (%s, %s);",
                (winner_id, loser_id),
            )
            elos = {row[0]: row[1] for row in cur.fetchall()}
            winner_delta, loser_delta = calculate_elo_delta(elos[winner_id], elos[loser_id], GLOBAL_ELO_K)

            cur.execute(
                "UPDATE role_info SET global_elo = global_elo + %s WHERE role_id = %s;",
                (winner_delta, winner_id),
            )
            cur.execute(
                "UPDATE role_info SET global_elo = global_elo + %s WHERE role_id = %s;",
                (loser_delta, loser_id),
            )
            cur.execute(
                """
                UPDATE role_info
                SET global_win_count = global_win_count + 1,
                    global_match_count = global_match_count + 1
                WHERE role_id = %s;
                """,
                (winner_id,),
            )
            cur.execute(
                "UPDATE role_info SET global_match_count = global_match_count + 1 WHERE role_id = %s;",
                (loser_id,),
            )
            return {
                "winner_delta": winner_delta,
                "loser_delta": loser_delta,
            }


def get_global_leaderboard(limit: int = LEADERBOARD_SNAPSHOT_LIMIT) -> Leaderboard:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(global_match_count), 0) / 2 FROM role_info;")
            vote_count = cur.fetchone()[0]
            cur.execute(
                f"""
                WITH previous_snapshot AS (
                    SELECT MAX(snapshot_date) AS snapshot_date
                    FROM bias_leaderboard_snapshots
                    WHERE scope_type = 'global'
                      AND scope_id = 0
                      AND snapshot_period = 'weekly'
                      AND captured_at <= NOW() - INTERVAL '24 hours'
                ),
                previous_ranks AS (
                    SELECT s.role_id, s.rank, s.snapshot_date
                    FROM bias_leaderboard_snapshots s
                    JOIN previous_snapshot p ON s.snapshot_date = p.snapshot_date
                    WHERE s.scope_type = 'global'
                      AND s.scope_id = 0
                      AND s.snapshot_period = 'weekly'
                )
                SELECT r.role_id, r.member_name, r.group_name, r.global_elo,
                       r.image_url, p.rank AS previous_rank,
                       r.global_match_count AS votes,
                       ps.snapshot_date AS movement_baseline_date
                FROM role_info r
                CROSS JOIN previous_snapshot ps
                LEFT JOIN previous_ranks p ON r.role_id = p.role_id
                WHERE {_ACTIVE_IDOL_PREDICATE}
                ORDER BY r.global_elo DESC, r.member_name, r.role_id
                LIMIT %s;
                """,
                (limit,),
            )
            return _build_leaderboard(cur.fetchall(), vote_count)


def get_global_group_leaderboard(limit: int = 15, top_n: int = 3) -> GroupLeaderboard:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(global_match_count), 0) / 2 FROM role_info;")
            vote_count = cur.fetchone()[0]
            cur.execute(
                f"""
                WITH idol_scores AS (
                    SELECT r.group_name, r.member_name, r.image_url,
                           r.global_elo AS elo,
                           r.global_match_count AS matches,
                           COUNT(*) OVER (PARTITION BY r.group_name) AS member_count,
                           ROW_NUMBER() OVER (
                               PARTITION BY r.group_name
                               ORDER BY r.global_elo DESC, r.member_name
                           ) AS member_rank
                    FROM role_info r
                    WHERE {_ACTIVE_IDOL_PREDICATE}
                      AND r.group_name IS NOT NULL
                      AND TRIM(r.group_name) != ''
                )
                SELECT group_name, ROUND(AVG(elo))::int AS elo,
                       MAX(member_count)::int AS member_count,
                       COUNT(*)::int AS ranked_member_count,
                       ARRAY_AGG(member_name ORDER BY elo DESC, member_name) AS top_members,
                       (ARRAY_AGG(image_url ORDER BY elo DESC, member_name))[1] AS image_url,
                       ARRAY_AGG(image_url ORDER BY elo DESC, member_name) AS member_images,
                       SUM(CASE WHEN member_rank <= %s THEN matches ELSE 0 END)::int AS votes
                FROM idol_scores
                WHERE member_rank <= %s
                GROUP BY group_name
                ORDER BY elo DESC, group_name
                LIMIT %s;
                """,
                (top_n, top_n, limit),
            )
            return _build_group_leaderboard(cur.fetchall(), vote_count, top_n)


# ---------------------------------------------------------------------------
# Weekly snapshots (sparse: only when top-N ranks/ELOs changed)
# ---------------------------------------------------------------------------


def _fetch_latest_snapshot_rows(cur, scope_type: str, scope_id: int, before_date) -> list:
    cur.execute(
        """
        SELECT role_id, rank, elo
        FROM bias_leaderboard_snapshots
        WHERE scope_type = %s AND scope_id = %s AND snapshot_period = 'weekly'
          AND snapshot_date = (
              SELECT MAX(snapshot_date) FROM bias_leaderboard_snapshots
              WHERE scope_type = %s AND scope_id = %s
                AND snapshot_period = 'weekly' AND snapshot_date < %s
          )
        ORDER BY rank;
        """,
        (scope_type, scope_id, scope_type, scope_id, before_date),
    )
    return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def _scope_has_snapshot(cur, scope_type: str, scope_id: int, snapshot_date) -> bool:
    cur.execute(
        """
        SELECT 1 FROM bias_leaderboard_snapshots
        WHERE scope_type = %s AND scope_id = %s
          AND snapshot_period = 'weekly' AND snapshot_date = %s
        LIMIT 1;
        """,
        (scope_type, scope_id, snapshot_date),
    )
    return cur.fetchone() is not None


def _insert_snapshot_if_changed(cur, snapshot_date, scope_type: str, scope_id: int, rows: list) -> bool:
    if not rows or _scope_has_snapshot(cur, scope_type, scope_id, snapshot_date):
        return False
    current = [(role_id, rank, elo) for role_id, rank, elo, _ in rows]
    if _fetch_latest_snapshot_rows(cur, scope_type, scope_id, snapshot_date) == current:
        return False
    cur.executemany(
        """
        INSERT INTO bias_leaderboard_snapshots (
            snapshot_date, snapshot_period, scope_type, scope_id,
            role_id, rank, elo, vote_count
        ) VALUES (%s, 'weekly', %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING;
        """,
        [(snapshot_date, scope_type, scope_id, role_id, rank, elo, vote_count) for role_id, rank, elo, vote_count in rows],
    )
    return True


def _fetch_global_snapshot_rows(cur, limit: int) -> list:
    cur.execute(
        f"""
        WITH ranked AS (
            SELECT r.role_id, r.global_elo AS elo,
                   ROW_NUMBER() OVER (
                       ORDER BY r.global_elo DESC, r.member_name, r.role_id
                   ) AS rank,
                   (SELECT (COALESCE(SUM(global_match_count), 0) / 2)::int
                    FROM role_info) AS vote_count
            FROM role_info r
            WHERE {_ACTIVE_IDOL_PREDICATE}
        )
        SELECT role_id, rank::int, elo, vote_count FROM ranked
        WHERE rank <= %s ORDER BY rank;
        """,
        (limit,),
    )
    return cur.fetchall()


def create_weekly_global_snapshot(
    snapshot_date: datetime.date | None = None,
    limit: int = LEADERBOARD_SNAPSHOT_LIMIT,
) -> bool:
    """Snapshot the global board for this KST week. Returns True if written."""
    snapshot_date = _week_start_kst(snapshot_date)
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            return _insert_snapshot_if_changed(
                cur, snapshot_date, "global", 0, _fetch_global_snapshot_rows(cur, limit)
            )
