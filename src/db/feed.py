"""Database queries for the public Hanni feed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.config.constants import (
    EPHEMERAL_MEDIA_HOSTS,
    INITIAL_REACT_CAP,
    MAX_FEED_ITEMS,
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


def get_feed_items(
    *,
    query: str | None = None,
    sort: FeedSort = "random",
    limit: int = 24,
    min_age: str,
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

        order_by = {
            "latest": "cl.uploaded_date DESC, cl.content_link_id DESC",
            "oldest": "cl.uploaded_date ASC, cl.content_link_id ASC",
            "top": "feed_score DESC, cl.uploaded_date DESC, cl.content_link_id DESC",
            "random": "random_weight DESC, cl.content_link_id DESC",
        }[sort]
        score_expression = """(
            LEAST(COALESCE(cl.initial_reaction_count, 0), %s)
            + COALESCE(cl.num_upvotes, 0)
            - COALESCE(cl.num_downvotes, 0)
        )::double precision
        """
        random_expression = f"RANDOM() * POWER(GREATEST({score_expression}, 1.0), %s)"

        # SELECT expressions precede the WHERE clause in SQL placeholder
        # order. The random sort embeds the score expression a second time.
        random_select = f",\n                    {random_expression} AS random_weight" if sort == "random" else ""
        select_params: list[object] = [INITIAL_REACT_CAP]
        if sort == "random":
            select_params = [INITIAL_REACT_CAP, INITIAL_REACT_CAP, SAMPLING_EXPONENT]
        query_params = [*select_params, *params, limit]

        with connection.cursor() as cursor:
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
                    recovery_dates.recovered_at{random_select}
                FROM content_links AS cl
                JOIN role_info AS ri ON ri.role_id = cl.role_id
                LEFT JOIN recovery_dates
                    ON recovery_dates.original_url = cl.original_url
                   AND recovery_dates.replacement_url = cl.url
                WHERE {' AND '.join(where)}
                ORDER BY {order_by}
                LIMIT %s
                """,
                query_params,
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
