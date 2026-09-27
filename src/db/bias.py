"""Idol Elo with a 12-point visitor/idol/day budget.

Catalog identities include idols without Discord roles. Ratings stay fractional;
only display scores are rounded. Pair deduplication, budgets and rating
updates commit together. Groups retain their legacy mapped membership.
"""

from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN


_KST = datetime.timezone(datetime.timedelta(hours=9))

LEADERBOARD_SNAPSHOT_LIMIT = 45
LEADERBOARD_PAGE_SIZE = 15
GLOBAL_ELO_K = 8

RANKED_MIN_MATCHES = 15  # Existing group-board eligibility only.
DAILY_IDOL_BUDGET = Decimal("12")
FRESH_FACE_LIMIT = 5


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


def calculate_elo_delta(winner_elo: float, loser_elo: float, k: float = 8) -> tuple[float, float]:
    expected_winner = 1 / (1 + 10 ** ((float(loser_elo) - float(winner_elo)) / 400))
    delta = k * (1 - expected_winner)
    return delta, -delta


def bounded_delta(winner_elo, loser_elo, k, winner_spent, loser_spent) -> Decimal:
    """Equal transfer bounded by both idols' remaining absolute movement."""
    proposed = Decimal(str(calculate_elo_delta(winner_elo, loser_elo, k)[0]))
    return max(Decimal(0), min(proposed, DAILY_IDOL_BUDGET - winner_spent,
                               DAILY_IDOL_BUDGET - loser_spent)).quantize(
        Decimal("0.0000000001"), rounding=ROUND_DOWN)


@dataclass(frozen=True)
class LeaderboardEntry:
    role_id: str
    member_name: str
    group_name: str
    elo: int
    image_url: str
    previous_rank: int | None = None
    votes: int = 0
    # Numbered rank among ranked entries only (None in the fresh strip).
    rank: int | None = None
    provisional: bool = False


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
    # Highest member score in the group. The sorter lineup orders groups by
    # this; the Groups board itself ranks by the top-3 average (elo).
    peak_elo: int = 0
    rank: int | None = None
    provisional: bool = False


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
    entries = []
    rank = 0
    for row in rows:
        votes = int(row[6] or 0) if len(row) > 6 else 0
        provisional = votes == 0
        if not provisional:
            rank += 1
        entries.append(
            LeaderboardEntry(
                role_id=row[0],
                member_name=row[1],
                group_name=row[2],
                elo=int(row[8]) if len(row) > 8 and row[8] is not None else int(row[3]),
                image_url=row[4],
                previous_rank=row[5] if len(row) > 5 else None,
                votes=votes,
                rank=rank if not provisional else None,
                provisional=provisional,
            )
        )
    return Leaderboard(
        entries=entries,
        vote_count=int(vote_count or 0),
        movement_baseline_date=baseline,
    )


def _build_group_leaderboard(rows, vote_count: int, top_n: int) -> GroupLeaderboard:
    entries = []
    rank = 0
    for row in rows:
        votes = int(row[7] or 0) if len(row) > 7 else 0
        provisional = votes < RANKED_MIN_MATCHES * top_n
        if not provisional:
            rank += 1
        entries.append(
            GroupLeaderboardEntry(
                group_name=row[0],
                elo=row[1],
                member_count=row[2],
                ranked_member_count=row[3],
                top_members=list(row[4] or []),
                image_url=row[5],
                votes=votes,
                top_member_images=list(row[6] or []) if len(row) > 6 else [],
                peak_elo=int(row[8]) if len(row) > 8 and row[8] is not None else 0,
                rank=rank if not provisional else None,
                provisional=provisional,
            )
        )
    return GroupLeaderboard(entries=entries, vote_count=int(vote_count or 0), top_n=top_n)


def _pool():
    from src.db import POOL

    return POOL


# Distinct pairings per visitor-day at which the global K-factor halves.
DAILY_DECAY_TAU = 250


def scaled_global_k(distinct_pairs: int) -> int:
    """Global K for a visitor's next new pairing after `distinct_pairs` today.

    K decreases hyperbolically (8 at 0, 4 at 250, 2 at 750; floor 1), so
    marathon sessions keep full local effect while their marginal global
    weight fades. Variable K is sound ELO: each matchup stays zero-sum.
    """
    if distinct_pairs < 0:
        distinct_pairs = 0
    return max(1, round(GLOBAL_ELO_K * DAILY_DECAY_TAU / (DAILY_DECAY_TAU + distinct_pairs)))


def pair_key_for(winner_id: str, loser_id: str) -> str:
    """Order-independent key for a matchup pair."""
    return f"{winner_id}/{loser_id}" if winner_id < loser_id else f"{loser_id}/{winner_id}"


def prune_visitor_pair_votes(retention_days: int = 2) -> int:
    """Prune old pair ballots and idol budgets; return only the pair-row count.

    The worker reports this as `pair_ballots_pruned`, not total deleted rows.
    Ballots only matter for their own UTC day (first-meeting check + daily
    volume count), so anything older is dead weight. Runs inside the weekly
    bias worker job — no schedule of its own.
    """
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM visitor_pair_votes WHERE day < CURRENT_DATE - %s;",
                (max(0, int(retention_days)),),
            )
            removed = int(cur.rowcount or 0)
            cur.execute("DELETE FROM visitor_idol_budget WHERE day < CURRENT_DATE - %s;",
                        (max(0, int(retention_days)),))
            return removed


def visitor_key(visitor_token: str) -> str:
    """Pseudonymous accounting key; never persist the raw browser cookie."""
    return hashlib.sha256(visitor_token.encode("utf-8")).hexdigest()


def record_sorter_vote(winner_id: str, loser_id: str, visitor_token: str = "",
                       day: datetime.date | None = None) -> dict | None:
    """Atomically accept one budgeted pair vote (or a no-op)."""
    if not winner_id or not loser_id or winner_id == loser_id or not visitor_token:
        return None
    visitor_token = visitor_key(visitor_token)
    day = day or datetime.datetime.now(datetime.timezone.utc).date()
    ids = sorted((winner_id, loser_id))
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            # Serialize a visitor's daily accounting; lock idol rows in a
            # stable order so different visitors cannot deadlock on A/B.
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));", (visitor_token,))
            cur.execute("""SELECT role_id, global_elo FROM idol_ratings
                           WHERE role_id = ANY(%s) AND TRIM(member_name) <> ''
                           ORDER BY role_id FOR UPDATE;""", (ids,))
            elos = dict(cur.fetchall())
            if len(elos) != 2:
                return {"recorded": False, "reason": "unknown_id"}
            cur.execute("""INSERT INTO visitor_pair_votes (visitor_token, day, pair_key)
                           VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING 1;""",
                        (visitor_token, day, pair_key_for(*ids)))
            if cur.fetchone() is None:
                return {"recorded": False, "reason": "repeat", "global_k": 0}
            cur.execute("""SELECT COUNT(*) FROM visitor_pair_votes
                           WHERE visitor_token = %s AND day = %s;""", (visitor_token, day))
            k = scaled_global_k(cur.fetchone()[0] - 1)
            cur.execute("""SELECT role_id, spent FROM visitor_idol_budget
                           WHERE visitor_token = %s AND day = %s AND role_id = ANY(%s);""",
                        (visitor_token, day, ids))
            spent = dict(cur.fetchall())
            delta = bounded_delta(elos[winner_id], elos[loser_id], k,
                                  spent.get(winner_id, Decimal(0)), spent.get(loser_id, Decimal(0)))
            if not delta:
                return {"recorded": False, "reason": "budget_exhausted", "global_k": 0}
            for role_id in ids:
                cur.execute("""INSERT INTO visitor_idol_budget (visitor_token, day, role_id, spent)
                               VALUES (%s, %s, %s, %s)
                               ON CONFLICT (visitor_token, day, role_id)
                               DO UPDATE SET spent = visitor_idol_budget.spent + EXCLUDED.spent;""",
                            (visitor_token, day, role_id, delta))
                cur.execute("""UPDATE idol_ratings SET global_elo = global_elo + %s,
                               global_match_count = global_match_count + 1,
                               global_win_count = global_win_count + %s WHERE role_id = %s;""",
                            (delta if role_id == winner_id else -delta,
                             int(role_id == winner_id), role_id))
            return {"recorded": True, "reason": "accepted", "global_k": k}


def get_global_leaderboard(limit: int = LEADERBOARD_SNAPSHOT_LIMIT,
                           include_provisional: bool = True) -> Leaderboard:
    """Return up to `limit` ranked idols plus an independent fresh sample."""
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(global_match_count), 0) / 2 FROM idol_ratings;")
            vote_count = cur.fetchone()[0]
            cur.execute(
                f"""
                WITH previous_snapshot AS (
                    SELECT MAX(snapshot_date) AS snapshot_date
                    FROM idol_leaderboard_snapshots
                    WHERE captured_at <= NOW() - INTERVAL '24 hours'
                ), candidates AS (
                    SELECT r.role_id, r.member_name, r.group_name, r.global_elo,
                           r.image_url, r.global_match_count,
                           ROUND(r.global_elo)::int AS score
                    FROM idol_ratings r WHERE {_ACTIVE_IDOL_PREDICATE}
                ), selected AS (
                    (SELECT * FROM candidates WHERE global_match_count > 0
                     ORDER BY score DESC, member_name, role_id LIMIT %s)
                    UNION ALL
                    (SELECT * FROM candidates WHERE global_match_count = 0
                     ORDER BY md5(role_id || %s), role_id LIMIT %s)
                )
                SELECT r.role_id, r.member_name, r.group_name, r.global_elo,
                       r.image_url, p.rank, r.global_match_count,
                       ps.snapshot_date, r.score
                FROM selected r CROSS JOIN previous_snapshot ps
                LEFT JOIN idol_leaderboard_snapshots p
                    ON p.role_id = r.role_id AND p.snapshot_date = ps.snapshot_date
                ORDER BY (r.global_match_count > 0) DESC, r.score DESC, r.member_name, r.role_id;
                """,
                (limit, datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
                 FRESH_FACE_LIMIT if include_provisional else 0),
            )
            return _build_leaderboard(cur.fetchall(), vote_count)


def get_global_group_leaderboard(limit: int = 15, top_n: int = 3) -> GroupLeaderboard:
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT COALESCE(SUM(r.global_match_count), 0) / 2
                           FROM idol_ratings r JOIN role_info legacy USING (role_id);""")
            vote_count = cur.fetchone()[0]
            cur.execute(
                f"""
                WITH idol_scores AS (
                    SELECT r.group_name, r.member_name, r.image_url,
                           -- Legacy mapped population, using the same live
                           -- idol score rather than applying shrinkage twice.
                           r.global_elo AS score,
                           r.global_match_count AS matches,
                           COUNT(*) OVER (PARTITION BY r.group_name) AS member_count,
                           ROW_NUMBER() OVER (
                               PARTITION BY r.group_name
                               ORDER BY r.global_elo DESC, r.member_name
                           ) AS member_rank
                    FROM (SELECT legacy.role_id, legacy.member_name, legacy.group_name,
                                 legacy.image_url, rating.global_elo, rating.global_match_count
                          FROM role_info legacy JOIN idol_ratings rating USING (role_id)) r
                    WHERE {_ACTIVE_IDOL_PREDICATE}
                      AND r.group_name IS NOT NULL
                      AND TRIM(r.group_name) != ''
                )
                SELECT group_name, ROUND(AVG(score))::int AS elo,
                       MAX(member_count)::int AS member_count,
                       COUNT(*)::int AS ranked_member_count,
                       ARRAY_AGG(member_name ORDER BY score DESC, member_name) AS top_members,
                       (ARRAY_AGG(image_url ORDER BY score DESC, member_name))[1] AS image_url,
                       ARRAY_AGG(image_url ORDER BY score DESC, member_name) AS member_images,
                       SUM(CASE WHEN member_rank <= %s THEN matches ELSE 0 END)::int AS votes,
                       MAX(score)::int AS peak_elo
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


def _insert_snapshot_if_changed(cur, snapshot_date, rows: list) -> bool:
    if not rows:
        return False
    cur.execute("SELECT 1 FROM idol_leaderboard_snapshots WHERE snapshot_date = %s LIMIT 1;",
                (snapshot_date,))
    if cur.fetchone() is not None:
        return False
    cur.execute(
        """SELECT role_id, rank, elo FROM idol_leaderboard_snapshots
           WHERE snapshot_date = (
               SELECT MAX(snapshot_date) FROM idol_leaderboard_snapshots
               WHERE snapshot_date < %s)
           ORDER BY rank;""",
        (snapshot_date,),
    )
    current = [(role_id, rank, elo) for role_id, rank, elo, _ in rows]
    if cur.fetchall() == current:
        return False
    cur.executemany(
        """INSERT INTO idol_leaderboard_snapshots
           (snapshot_date, role_id, rank, elo, vote_count)
           VALUES (%s, %s, %s, %s, %s);""",
        [(snapshot_date, *row) for row in rows],
    )
    return True


def _fetch_global_snapshot_rows(cur, limit: int) -> list:
    cur.execute(
        f"""
        WITH scores AS (
            SELECT r.role_id, r.member_name,
                   ROUND(r.global_elo)::int AS elo
            FROM idol_ratings r
            WHERE {_ACTIVE_IDOL_PREDICATE} AND r.global_match_count > 0
        ), ranked AS (
            SELECT role_id, elo, ROW_NUMBER() OVER (
                ORDER BY elo DESC, member_name, role_id) AS rank
            FROM scores
        )
        SELECT role_id, rank::int, elo,
               (SELECT (COALESCE(SUM(global_match_count), 0) / 2)::int FROM idol_ratings)
        FROM ranked WHERE rank <= %s ORDER BY rank;
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
            # Lock before reading either the board or existing snapshots.
            # The entire week's snapshot commits or rolls back as one unit.
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));",
                        (f"idol-snapshot:{snapshot_date.isoformat()}",))
            return _insert_snapshot_if_changed(
                cur, snapshot_date, _fetch_global_snapshot_rows(cur, limit)
            )
