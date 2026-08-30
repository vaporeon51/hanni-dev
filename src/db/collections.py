"""Resolve exact Discord posts and legacy posting bursts into content sets."""

from __future__ import annotations

from dataclasses import dataclass

from src.config.constants import (
    EPHEMERAL_MEDIA_HOSTS,
    INITIAL_REACT_CAP,
    MAX_FEED_ITEMS,
    MIN_CONTENT_AGE,
    REPORT_THRESHOLD,
)
from src.db import POOL
from src.db.feed import FeedItem

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
