"""Resolve exact Discord posts and legacy posting bursts into content sets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.config.constants import (
    EPHEMERAL_MEDIA_HOSTS,
    INITIAL_REACT_CAP,
    MAX_FEED_ITEMS,
    MIN_CONTENT_AGE,
    RANDOM_FRESHNESS_DECAY_DAYS,
    RANDOM_FRESHNESS_FULL_DAYS,
    RANDOM_FRESHNESS_MAX_BOOST,
    REPORT_THRESHOLD,
    SAMPLING_EXPONENT,
)
from src.db import POOL
from src.db.feed import FeedItem, FeedSort, _role_ids_for_query

LEGACY_CONTINUATION_MINUTES = 2


@dataclass(frozen=True)
class CollectionPreview:
    url: str
    count: int


@dataclass(frozen=True)
class ContentCollection:
    label: str
    items: tuple[FeedItem, ...]


@dataclass(frozen=True)
class ContentSet:
    collection_of: int
    label: str
    items: tuple[FeedItem, ...]
    set_date: datetime | None = None


@dataclass(frozen=True)
class _Anchor:
    content_link_id: int
    role_id: str
    author_id: str | None
    root_message_id: str | None
    url: str
    member_name: str | None
    group_name: str | None

    @property
    def label(self) -> str:
        member = (self.member_name or "").strip()
        group = (self.group_name or "").strip()
        if member and group and member.casefold() != group.casefold():
            return f"{member} - {group}"
        return member or group or self.role_id


def _ephemeral_filters(alias: str = "cl") -> tuple[list[str], list[str]]:
    hosts = sorted(EPHEMERAL_MEDIA_HOSTS)
    return ([f"LOWER({alias}.url) NOT LIKE %s" for _host in hosts], [f"%://{host}/%" for host in hosts])


def _live_filters(alias: str = "cl", role_alias: str = "ri") -> tuple[list[str], list[object]]:
    filters = [
        f"{alias}.is_dead = FALSE",
        f"{alias}.num_reports < %s",
        f"{alias}.uploaded_date IS NOT NULL",
        f"{role_alias}.birthday IS NOT NULL",
        f"{alias}.uploaded_date > {role_alias}.birthday + %s::interval",
    ]
    params: list[object] = [REPORT_THRESHOLD, MIN_CONTENT_AGE]
    ephemeral_filters, ephemeral_params = _ephemeral_filters(alias)
    return [*filters, *ephemeral_filters], [*params, *ephemeral_params]


def _get_anchor(connection, content_link_id: int) -> _Anchor | None:
    filters, params = _live_filters()
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                cl.content_link_id,
                cl.role_id,
                cl.author_id,
                cl.root_message_id,
                cl.url,
                ri.member_name,
                ri.group_name
            FROM content_links AS cl
            JOIN role_info AS ri ON ri.role_id = cl.role_id
            WHERE cl.content_link_id = %s
              AND {' AND '.join(filters)}
            LIMIT 1
            """,
            (content_link_id, *params),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return _Anchor(
        content_link_id=int(row[0]),
        role_id=str(row[1]),
        author_id=str(row[2]) if row[2] is not None else None,
        root_message_id=str(row[3]) if row[3] is not None else None,
        url=str(row[4]),
        member_name=row[5],
        group_name=row[6],
    )


def _exact_member_ids(connection, anchor: _Anchor) -> list[int]:
    filters, params = _live_filters()
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT content_link_id
            FROM (
                SELECT DISTINCT ON (cl.url)
                    cl.content_link_id,
                    cl.url,
                    cl.uploaded_date
                FROM content_links AS cl
                JOIN role_info AS ri ON ri.role_id = cl.role_id
                WHERE cl.role_id = %s
                  AND cl.root_message_id = %s
                  AND {' AND '.join(filters)}
                ORDER BY cl.url, cl.uploaded_date, cl.content_link_id
            ) AS distinct_links
            ORDER BY uploaded_date, content_link_id
            LIMIT %s
            """,
            (anchor.role_id, anchor.root_message_id, *params, MAX_FEED_ITEMS),
        )
        return [int(row[0]) for row in cursor.fetchall()]


def _legacy_member_ids(connection, anchor: _Anchor) -> list[int]:
    if not anchor.author_id:
        return [anchor.content_link_id]
    filters, params = _live_filters()
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH sequenced AS (
                SELECT
                    content_link_id,
                    uploaded_date,
                    LAG(uploaded_date) OVER (
                        ORDER BY uploaded_date, content_link_id
                    ) AS previous_date
                FROM content_links
                WHERE role_id = %s
                  AND author_id = %s
                  AND root_message_id IS NULL
                  AND uploaded_date IS NOT NULL
            ),
            marked AS (
                SELECT
                    content_link_id,
                    uploaded_date,
                    CASE
                        WHEN previous_date IS NULL
                          OR uploaded_date - previous_date > make_interval(mins => %s)
                        THEN 1 ELSE 0
                    END AS starts_group
                FROM sequenced
            ),
            grouped AS (
                SELECT
                    content_link_id,
                    SUM(starts_group) OVER (
                        ORDER BY uploaded_date, content_link_id
                        ROWS UNBOUNDED PRECEDING
                    ) AS group_number
                FROM marked
            ),
            anchor_group AS (
                SELECT group_number
                FROM grouped
                WHERE content_link_id = %s
            )
            SELECT content_link_id
            FROM (
                SELECT DISTINCT ON (cl.url)
                    cl.content_link_id,
                    cl.url,
                    cl.uploaded_date
                FROM grouped
                JOIN anchor_group USING (group_number)
                JOIN content_links AS cl USING (content_link_id)
                JOIN role_info AS ri ON ri.role_id = cl.role_id
                WHERE {' AND '.join(filters)}
                ORDER BY cl.url, cl.uploaded_date, cl.content_link_id
            ) AS distinct_links
            ORDER BY uploaded_date, content_link_id
            LIMIT %s
            """,
            (
                anchor.role_id,
                anchor.author_id,
                LEGACY_CONTINUATION_MINUTES,
                anchor.content_link_id,
                *params,
                MAX_FEED_ITEMS,
            ),
        )
        return [int(row[0]) for row in cursor.fetchall()]


def _member_ids(connection, anchor: _Anchor) -> list[int]:
    if anchor.root_message_id:
        return _exact_member_ids(connection, anchor)
    return _legacy_member_ids(connection, anchor)


def _items_for_ids(connection, content_link_ids: list[int]) -> tuple[FeedItem, ...]:
    if not content_link_ids:
        return ()
    with connection.cursor() as cursor:
        cursor.execute(
            """
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
                (
                    LEAST(COALESCE(cl.initial_reaction_count, 0), %s)
                    + COALESCE(cl.num_upvotes, 0)
                    - COALESCE(cl.num_downvotes, 0)
                )::double precision AS feed_score,
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
            WHERE cl.content_link_id = ANY(%s)
            ORDER BY cl.uploaded_date, cl.content_link_id
            """,
            (INITIAL_REACT_CAP, content_link_ids),
        )
        rows = cursor.fetchall()
    return tuple(
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
    )


def get_collection_preview(content_link_id: int) -> CollectionPreview | None:
    with POOL.connection() as connection:
        anchor = _get_anchor(connection, content_link_id)
        if anchor is None:
            return None
        return CollectionPreview(url=anchor.url, count=len(_member_ids(connection, anchor)))


def get_collection(content_link_id: int) -> ContentCollection | None:
    with POOL.connection() as connection:
        anchor = _get_anchor(connection, content_link_id)
        if anchor is None:
            return None
        items = _items_for_ids(connection, _member_ids(connection, anchor))
        return ContentCollection(label=anchor.label, items=items)


def get_collection_feed(
    *,
    query: str | None = None,
    sort: FeedSort = "latest",
    limit: int = 15,
    min_age: str,
    cursor_date: datetime | None = None,
    cursor_id: int | None = None,
) -> list[ContentSet]:
    """Return multi-link parent posts for the set-oriented feed.

    Search results use exact Discord root-message boundaries. That keeps a set
    semantically precise and lets the query hydrate all selected sets in one
    batch. The existing per-item collection view retains its legacy burst
    fallback for rows that predate root-message ingestion.
    """

    if sort not in {"random", "latest", "oldest", "top"}:
        raise ValueError(f"Unsupported collection sort: {sort}")
    if (cursor_date is None) != (cursor_id is None):
        raise ValueError("Collection cursor date and id must be provided together")
    if cursor_date is not None and sort not in {"latest", "oldest"}:
        raise ValueError("Collection cursors require chronological sorting")
    # The API may request one extra set to determine whether another page exists.
    limit = max(1, min(int(limit), MAX_FEED_ITEMS + 1))
    # Exact root-message sets are already strongly distinct, so a modest
    # over-fetch is enough for membership deduplication. Keeping this window
    # bounded avoids hydrating 30 complete sets just to show the first five.
    candidate_limit = min(max(limit * 3, 12), MAX_FEED_ITEMS * 3)

    with POOL.connection() as connection:
        role_ids = None
        if query and query.strip() and query.strip().lower() not in {"r", "random", "a", "all"}:
            role_ids = _role_ids_for_query(connection, query, min_age)
            if not role_ids:
                return []

        filters, filter_params = _live_filters()
        where = list(filters)
        if role_ids is not None:
            where.append("cl.role_id = ANY(%s)")
            filter_params.append(role_ids)

        order_by = {
            "latest": "set_date DESC, anchor_id DESC",
            "oldest": "set_date ASC, anchor_id ASC",
            "top": "set_score DESC, set_date DESC, anchor_id DESC",
        }.get(sort)
        random_order = """RANDOM()
            * POWER(GREATEST(set_random_score, 1.0), %s)
            * (
                1.0 + %s * EXP(
                    -GREATEST(
                        EXTRACT(EPOCH FROM (NOW() - set_date)) / 86400.0 - %s,
                        0.0
                    ) / %s
                )
            ) DESC
        """
        extra_params: tuple[object, ...] = ()
        if sort == "random":
            order_by = random_order
            extra_params = (
                SAMPLING_EXPONENT,
                RANDOM_FRESHNESS_MAX_BOOST,
                RANDOM_FRESHNESS_FULL_DAYS,
                RANDOM_FRESHNESS_DECAY_DAYS,
            )
        cursor_clause = ""
        cursor_params: tuple[object, ...] = ()
        if cursor_date is not None and cursor_id is not None:
            comparison = "<" if sort == "latest" else ">"
            cursor_clause = f"AND (set_date, content_link_id) {comparison} (%s, %s)"
            cursor_params = (cursor_date, cursor_id)

        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH eligible AS (
                    SELECT
                        cl.content_link_id,
                        cl.role_id,
                        cl.author_id,
                        cl.root_message_id,
                        cl.url,
                        cl.uploaded_date,
                        ri.member_name,
                        ri.group_name,
                        (
                            LEAST(COALESCE(cl.initial_reaction_count, 0), %s)
                            + COALESCE(cl.num_upvotes, 0)
                            - COALESCE(cl.num_downvotes, 0)
                        )::double precision AS item_score,
                        (
                            LEAST(COALESCE(cl.initial_reaction_count, 0)::double precision / 3.0, %s)
                            + COALESCE(cl.num_upvotes, 0)
                            - COALESCE(cl.num_downvotes, 0)
                        )::double precision AS item_random_score
                    FROM content_links AS cl
                    JOIN role_info AS ri ON ri.role_id = cl.role_id
                    WHERE cl.root_message_id IS NOT NULL
                      AND {' AND '.join(where)}
                ),
                grouped AS (
                    SELECT
                        role_id,
                        root_message_id,
                        MIN(uploaded_date) AS set_date,
                        MAX(item_score) AS set_score,
                        MAX(item_random_score) AS set_random_score
                    FROM eligible
                    GROUP BY role_id, root_message_id
                    HAVING COUNT(DISTINCT url) >= 2
                ),
                ranked AS (
                    SELECT
                        eligible.*,
                        grouped.set_date,
                        grouped.set_score,
                        grouped.set_random_score,
                        ROW_NUMBER() OVER (
                            PARTITION BY eligible.role_id, eligible.root_message_id
                            ORDER BY eligible.uploaded_date, eligible.content_link_id
                        ) AS anchor_rank
                    FROM eligible
                    JOIN grouped USING (role_id, root_message_id)
                )
                SELECT
                    content_link_id AS anchor_id,
                    role_id,
                    author_id,
                    root_message_id,
                    url,
                    member_name,
                    group_name,
                    set_date,
                    set_score,
                    set_random_score
                FROM ranked
                WHERE anchor_rank = 1
                  {cursor_clause}
                ORDER BY {order_by}
                LIMIT %s
                """,
                (
                    INITIAL_REACT_CAP,
                    INITIAL_REACT_CAP,
                    *filter_params,
                    *cursor_params,
                    *extra_params,
                    candidate_limit,
                ),
            )
            rows = cursor.fetchall()

        anchors = [
            _Anchor(
                content_link_id=int(row[0]),
                role_id=str(row[1]),
                author_id=str(row[2]) if row[2] is not None else None,
                root_message_id=str(row[3]),
                url=str(row[4]),
                member_name=row[5],
                group_name=row[6],
            )
            for row in rows
        ]
        set_dates = {int(row[0]): row[7] for row in rows}
        if not anchors:
            return []

        member_filters, member_filter_params = _live_filters()
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH wanted AS (
                    SELECT *
                    FROM unnest(%s::text[], %s::text[])
                        AS selected(role_id, root_message_id)
                ),
                distinct_links AS (
                    SELECT DISTINCT ON (cl.role_id, cl.root_message_id, cl.url)
                        cl.role_id,
                        cl.root_message_id,
                        cl.content_link_id,
                        cl.uploaded_date
                    FROM content_links AS cl
                    JOIN wanted
                      ON wanted.role_id = cl.role_id
                     AND wanted.root_message_id = cl.root_message_id
                    JOIN role_info AS ri ON ri.role_id = cl.role_id
                    WHERE {' AND '.join(member_filters)}
                    ORDER BY
                        cl.role_id,
                        cl.root_message_id,
                        cl.url,
                        cl.uploaded_date,
                        cl.content_link_id
                ),
                numbered AS (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY role_id, root_message_id
                            ORDER BY uploaded_date, content_link_id
                        ) AS member_rank
                    FROM distinct_links
                )
                SELECT role_id, root_message_id, content_link_id
                FROM numbered
                WHERE member_rank <= %s
                ORDER BY role_id, root_message_id, member_rank
                """,
                (
                    [anchor.role_id for anchor in anchors],
                    [anchor.root_message_id for anchor in anchors],
                    *member_filter_params,
                    MAX_FEED_ITEMS,
                ),
            )
            member_rows = cursor.fetchall()

        ids_by_set: dict[tuple[str, str], list[int]] = {}
        for role_id, root_message_id, content_link_id in member_rows:
            ids_by_set.setdefault((str(role_id), str(root_message_id)), []).append(int(content_link_id))
        all_member_ids = [content_link_id for ids in ids_by_set.values() for content_link_id in ids]
        item_by_id = {
            item.content_link_id: item
            for item in _items_for_ids(connection, all_member_ids)
        }

        results: list[ContentSet] = []
        seen_memberships: set[tuple[str, ...]] = set()
        for anchor in anchors:
            content_link_ids = ids_by_set.get((anchor.role_id, anchor.root_message_id or ""), [])
            items = tuple(item_by_id[item_id] for item_id in content_link_ids if item_id in item_by_id)
            if len(items) < 2:
                continue
            membership = tuple(sorted(item.url for item in items))
            if membership in seen_memberships:
                continue
            seen_memberships.add(membership)
            results.append(
                ContentSet(
                    collection_of=anchor.content_link_id,
                    label=anchor.label,
                    items=items,
                    set_date=set_dates[anchor.content_link_id],
                )
            )
            if len(results) >= limit:
                break
        return results
