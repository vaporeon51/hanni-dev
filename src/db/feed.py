"""Database queries for the public Hanni feed."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.config.constants import (
    EPHEMERAL_MEDIA_HOSTS,
    INITIAL_REACT_CAP,
    MAX_FEED_ITEMS,
    RANDOM_FRESHNESS_DECAY_DAYS,
    RANDOM_FRESHNESS_FULL_DAYS,
    RANDOM_FRESHNESS_MAX_BOOST,
    REPORT_THRESHOLD,
    SAMPLING_EXPONENT,
)
from src.db import POOL

FeedSort = Literal["random", "latest", "oldest", "top"]


def _ephemeral_url_filters(alias: str = "cl") -> tuple[list[str], list[str]]:
    filters = [f"LOWER({alias}.url) NOT LIKE %s" for _host in sorted(EPHEMERAL_MEDIA_HOSTS)]
    params = [f"%://{host}/%" for host in sorted(EPHEMERAL_MEDIA_HOSTS)]
    return filters, params


@dataclass(frozen=True)
class FeedItem:
    content_link_id: int
    role_id: str
    member_name: str | None
    group_name: str | None
    url: str
    original_url: str | None
    uploaded_date: datetime | None
    score: float
    upvotes: int = 0
    downvotes: int = 0
    reports: int = 0
    recovered_at: datetime | None = None
    recovery_generation: int = 0

    @property
    def label(self) -> str:
        member = (self.member_name or "").strip()
        group = (self.group_name or "").strip()
        if member and group and member.casefold() != group.casefold():
            return f"{member} - {group}"
        return member or group or self.role_id


def _role_ids_for_query(connection, query: str, min_age: str) -> list[str]:
    """Resolve a query using the old token-based best-match behavior."""

    with connection.cursor() as cursor:
        cursor.execute(
            r"""
            WITH query AS (
                SELECT string_to_array(
                    regexp_replace(LOWER(TRIM(%s)), '[^a-zA-Z0-9\s]', '', 'g'),
                    ' '
                ) AS terms
            ),
            matches AS (
                SELECT
                    role_id,
                    (
                        SELECT COUNT(*)
                        FROM unnest(member_group_array) AS mga
                        WHERE mga = ANY (query.terms)
                    ) AS match_count
                FROM role_info, query
                WHERE birthday IS NOT NULL
                  AND NOW() > birthday + %s::interval
            ),
            maxmatches AS (
                SELECT MAX(match_count) AS max_matches
                FROM matches
            )
            SELECT role_id
            FROM matches
            JOIN maxmatches ON matches.match_count = maxmatches.max_matches
            WHERE matches.match_count > 0
            ORDER BY RANDOM(), role_id
            LIMIT 100
            """,
            (query, min_age),
        )
        return [str(row[0]) for row in cursor.fetchall()]


def _eligible_role_capacities(connection, where: list[str], params: list[object]) -> list[tuple[str, int]]:
    """Return eligible link counts so random role draws can always be fulfilled."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT cl.role_id, COUNT(*)::integer AS eligible_count
            FROM content_links AS cl
            JOIN role_info AS ri ON ri.role_id = cl.role_id
            WHERE {' AND '.join(where)}
            GROUP BY cl.role_id
            """,
            params,
        )
        return [(str(row[0]), int(row[1])) for row in cursor.fetchall() if int(row[1]) > 0]


def _draw_role_slots(capacities: list[tuple[str, int]], limit: int) -> list[str]:
    """Draw roles uniformly while never requesting more links than they have."""

    remaining = {role_id: min(count, limit) for role_id, count in capacities if count > 0}
    selected: list[str] = []
    while remaining and len(selected) < limit:
        role_id = random.choice(tuple(remaining))
        selected.append(role_id)
        remaining[role_id] -= 1
        if remaining[role_id] == 0:
            del remaining[role_id]
    return selected


def get_feed_items(
    *,
    query: str | None = None,
    sort: FeedSort = "random",
    limit: int = 24,
    min_age: str,
    recent_urls: tuple[str, ...] = (),
    exclude_recent: bool = False,
) -> list[FeedItem]:
    """Return live, low-report content for the web feed.

    ``content_link_id`` is used as the stable client-side identity for feedback
    actions and future feed updates.
    """

    if sort not in {"random", "latest", "oldest", "top"}:
        raise ValueError(f"Unsupported feed sort: {sort}")
    limit = max(1, min(int(limit), MAX_FEED_ITEMS))

    with POOL.connection() as connection:
        role_ids = None
        if query and query.strip() and query.strip().lower() not in {"r", "random", "a", "all"}:
            role_ids = _role_ids_for_query(connection, query, min_age)
            if not role_ids:
                return []

        where = [
            "cl.is_dead = FALSE",
            "cl.num_reports < %s",
            "cl.uploaded_date IS NOT NULL",
            "ri.birthday IS NOT NULL",
            "cl.uploaded_date > ri.birthday + %s::interval",
        ]
        params: list[object] = [REPORT_THRESHOLD, min_age]
        ephemeral_filters, ephemeral_params = _ephemeral_url_filters()
        where.extend(ephemeral_filters)
        params.extend(ephemeral_params)
        if role_ids is not None:
            where.append("cl.role_id = ANY(%s)")
            params.append(role_ids)
        if exclude_recent and recent_urls:
            where.append("NOT (cl.url = ANY(%s))")
            params.append(list(recent_urls))

        order_by = {
            "latest": "cl.uploaded_date DESC, cl.content_link_id DESC",
            "oldest": "cl.uploaded_date ASC, cl.content_link_id ASC",
            "top": "feed_score DESC, cl.uploaded_date DESC, cl.content_link_id DESC",
        }.get(sort)
        score_expression = """(
            LEAST(COALESCE(cl.initial_reaction_count, 0), %s)
            + COALESCE(cl.num_upvotes, 0)
            - COALESCE(cl.num_downvotes, 0)
        )::double precision
        """
        random_score_expression = """(
            LEAST(COALESCE(cl.initial_reaction_count, 0)::double precision / 3.0, %s)
            + COALESCE(cl.num_upvotes, 0)
            - COALESCE(cl.num_downvotes, 0)
        )::double precision
        """
        freshness_expression = """(
            1.0 + %s * EXP(
                -GREATEST(
                    EXTRACT(EPOCH FROM (NOW() - cl.uploaded_date)) / 86400.0 - %s,
                    0.0
                ) / %s
            )
        )"""
        random_expression = (
            f"RANDOM() * POWER(GREATEST({random_score_expression}, 1.0), %s) * {freshness_expression}"
        )

        with connection.cursor() as cursor:
            if sort == "random":
                capacities = _eligible_role_capacities(connection, where, params)
                role_slots = _draw_role_slots(capacities, limit)
                if not role_slots:
                    return []
                role_counts = Counter(role_slots)
                cursor.execute(
                    f"""
                    WITH role_counts AS (
                        SELECT *
                        FROM unnest(%s::text[], %s::integer[]) AS selected(role_id, desired_count)
                    ),
                    recovery_dates AS (
                        SELECT
                            original_url,
                            replacement_url,
                            MAX(finished_at) AS recovered_at
                        FROM content_link_recovery_items
                        WHERE status = 'updated'
                        GROUP BY original_url, replacement_url
                    ),
                    ranked_links AS (
                        SELECT
                            cl.content_link_id,
                            cl.role_id,
                            ri.member_name,
                            ri.group_name,
                            cl.url,
                            cl.original_url,
                            cl.uploaded_date,
                            {score_expression} AS feed_score,
                            cl.num_upvotes,
                            cl.num_downvotes,
                            cl.num_reports,
                            cl.recovery_generation,
                            recovery_dates.recovered_at,
                            role_counts.desired_count,
                            ROW_NUMBER() OVER (
                                PARTITION BY cl.role_id
                                ORDER BY
                                    cl.url = ANY(%s) ASC,
                                    {random_expression} DESC,
                                    cl.content_link_id DESC
                            ) AS role_rank
                        FROM content_links AS cl
                        JOIN role_info AS ri ON ri.role_id = cl.role_id
                        JOIN role_counts ON role_counts.role_id = cl.role_id
                        LEFT JOIN recovery_dates
                            ON recovery_dates.original_url = cl.original_url
                           AND recovery_dates.replacement_url = cl.url
                        WHERE {' AND '.join(where)}
                    )
                    SELECT
                        content_link_id,
                        role_id,
                        member_name,
                        group_name,
                        url,
                        original_url,
                        uploaded_date,
                        feed_score,
                        num_upvotes,
                        num_downvotes,
                        num_reports,
                        recovery_generation,
                        recovered_at
                    FROM ranked_links
                    WHERE role_rank <= desired_count
                    ORDER BY RANDOM()
                    LIMIT %s
                    """,
                    (
                        list(role_counts),
                        list(role_counts.values()),
                        INITIAL_REACT_CAP,
                        list(recent_urls),
                        INITIAL_REACT_CAP,
                        SAMPLING_EXPONENT,
                        RANDOM_FRESHNESS_MAX_BOOST,
                        RANDOM_FRESHNESS_FULL_DAYS,
                        RANDOM_FRESHNESS_DECAY_DAYS,
                        *params,
                        limit,
                    ),
                )
            else:
                cursor.execute(
                    f"""
                    WITH recovery_dates AS (
                        SELECT
                            original_url,
                            replacement_url,
                            MAX(finished_at) AS recovered_at
                        FROM content_link_recovery_items
                        WHERE status = 'updated'
                        GROUP BY original_url, replacement_url
                    )
                    SELECT
                        cl.content_link_id,
                        cl.role_id,
                        ri.member_name,
                        ri.group_name,
                        cl.url,
                        cl.original_url,
                        cl.uploaded_date,
                        {score_expression} AS feed_score,
                        cl.num_upvotes,
                        cl.num_downvotes,
                        cl.num_reports,
                        cl.recovery_generation,
                        recovery_dates.recovered_at
                    FROM content_links AS cl
                    JOIN role_info AS ri ON ri.role_id = cl.role_id
                    LEFT JOIN recovery_dates
                        ON recovery_dates.original_url = cl.original_url
                       AND recovery_dates.replacement_url = cl.url
                    WHERE {' AND '.join(where)}
                    ORDER BY {order_by}
                    LIMIT %s
                    """,
                    (INITIAL_REACT_CAP, *params, limit),
                )
            rows = cursor.fetchall()

    return [
        FeedItem(
            content_link_id=int(row[0]),
            role_id=str(row[1]),
            member_name=row[2],
            group_name=row[3],
            url=str(row[4]),
            original_url=row[5],
            uploaded_date=row[6],
            score=float(row[7] or 0),
            upvotes=int(row[8] or 0),
            downvotes=int(row[9] or 0),
            reports=int(row[10] or 0),
            recovery_generation=int(row[11] or 0),
            recovered_at=row[12],
        )
        for row in rows
    ]


def get_role_suggestions(*, query: str, limit: int = 8, min_age: str) -> list[dict[str, str | None]]:
    """Return lightweight member/group suggestions for the search box."""

    query = query.strip()
    if not query:
        return []
    limit = max(1, min(int(limit), 20))
    ephemeral_filters, ephemeral_params = _ephemeral_url_filters()
    ephemeral_sql = "\n                    " + "\n                    ".join(f"AND {item}" for item in ephemeral_filters)
    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            rf"""
            WITH query AS (
                SELECT string_to_array(
                    regexp_replace(LOWER(TRIM(%s)), '[^a-zA-Z0-9\s]', '', 'g'),
                    ' '
                ) AS terms
            ),
            matches AS (
                SELECT
                    role_id,
                    member_name,
                    group_name,
                    (
                        SELECT COUNT(*)
                        FROM unnest(member_group_array) AS mga
                        WHERE mga = ANY (query.terms)
                    ) AS match_count
                FROM role_info, query
                WHERE birthday IS NOT NULL
                  AND NOW() > birthday + %s::interval
            ),
            maxmatches AS (
                SELECT MAX(match_count) AS max_matches
                FROM matches
            )
            SELECT role_id, member_name, group_name
            FROM matches
            JOIN maxmatches ON matches.match_count = maxmatches.max_matches
            WHERE matches.match_count > 0
              AND EXISTS (
                  SELECT 1
                  FROM content_links AS cl
                  WHERE cl.role_id = matches.role_id
                    AND cl.is_dead = FALSE
                    AND cl.num_reports < %s
                    {ephemeral_sql}
              )
            ORDER BY RANDOM(), role_id
            LIMIT %s
            """,
            (query, min_age, REPORT_THRESHOLD, *ephemeral_params, limit),
        )
        return [
            {"role_id": str(row[0]), "member_name": row[1], "group_name": row[2]}
            for row in cursor.fetchall()
        ]
