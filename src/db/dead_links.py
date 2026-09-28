"""Persistent state for Discord-based dead-link checks."""

from __future__ import annotations

from dataclasses import dataclass

from src.config.constants import (
    CONTENT_RECOVERY_MAX_GENERATION,
    EPHEMERAL_MEDIA_HOSTS,
    MIN_CONTENT_AGE,
    REPORT_THRESHOLD,
)
from src.db import POOL


@dataclass(frozen=True)
class DeadLinkCandidate:
    url: str
    content_link_count: int
    role_labels: tuple[str, ...]


def get_due_urls(
    *,
    limit: int,
    min_interval_seconds: int,
    min_age: str = MIN_CONTENT_AGE,
) -> list[DeadLinkCandidate]:
    """Return age-eligible URLs whose Discord check interval has elapsed."""

    limit = max(1, int(limit))
    interval = max(0, int(min_interval_seconds))
    excluded_hosts = sorted(EPHEMERAL_MEDIA_HOSTS)
    excluded_sql = "\n              " + "\n              ".join(
        "AND LOWER(cl.url) NOT LIKE %s" for _host in excluded_hosts
    )
    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH eligible_rows AS (
                SELECT
                    cl.url,
                    cl.last_checked_at,
                    CASE
                        WHEN ri.member_name IS NOT NULL AND ri.group_name IS NOT NULL
                            THEN ri.member_name || ' (' || ri.group_name || ')'
                        ELSE COALESCE(ri.member_name, ri.group_name, cl.role_id)
                    END AS role_label
                FROM content_links AS cl
                JOIN role_info AS ri ON ri.role_id = cl.role_id
                WHERE cl.is_dead = FALSE
                  AND cl.num_reports < %s
                  AND cl.url IS NOT NULL
                  AND cl.uploaded_date IS NOT NULL
                  AND cl.uploaded_date > ri.birthday + %s::interval
                  {excluded_sql}
                  AND (
                        cl.last_checked_at IS NULL
                     OR cl.last_checked_at <= NOW() - (%s * INTERVAL '1 second')
                  )
            )
            SELECT
                url,
                COUNT(*)::integer AS content_link_count,
                array_agg(DISTINCT role_label ORDER BY role_label) AS role_labels
            FROM eligible_rows
            GROUP BY url
            ORDER BY MIN(last_checked_at) NULLS FIRST, url
            LIMIT %s
            """,
            (
                REPORT_THRESHOLD,
                min_age,
                *(f"%://{host}/%" for host in excluded_hosts),
                interval,
                limit,
            ),
        )
        return [
            DeadLinkCandidate(
                url=str(row[0]),
                content_link_count=int(row[1]),
                role_labels=tuple(str(label) for label in (row[2] or ())),
            )
            for row in cursor.fetchall()
        ]


def get_candidates_by_urls(
    urls: list[str],
    *,
    min_age: str = MIN_CONTENT_AGE,
) -> list[DeadLinkCandidate]:
    """Return still-eligible candidates in the supplied priority order."""

    if not urls:
        return []
    excluded_hosts = sorted(EPHEMERAL_MEDIA_HOSTS)
    excluded_sql = "\n                  " + "\n                  ".join(
        "AND LOWER(cl.url) NOT LIKE %s" for _host in excluded_hosts
    )
    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH requested AS (
                SELECT url, position
                FROM unnest(%s::TEXT[]) WITH ORDINALITY AS queued(url, position)
            ),
            eligible_rows AS (
                SELECT
                    requested.url,
                    requested.position,
                    CASE
                        WHEN ri.member_name IS NOT NULL AND ri.group_name IS NOT NULL
                            THEN ri.member_name || ' (' || ri.group_name || ')'
                        ELSE COALESCE(ri.member_name, ri.group_name, cl.role_id)
                    END AS role_label
                FROM requested
                JOIN content_links AS cl ON cl.url = requested.url
                JOIN role_info AS ri ON ri.role_id = cl.role_id
                WHERE cl.is_dead = FALSE
                  AND cl.num_reports < %s
                  AND cl.uploaded_date IS NOT NULL
                  AND cl.uploaded_date > ri.birthday + %s::interval
                  {excluded_sql}
            )
            SELECT
                url,
                COUNT(*)::integer AS content_link_count,
                array_agg(DISTINCT role_label ORDER BY role_label) AS role_labels,
                MIN(position) AS position
            FROM eligible_rows
            GROUP BY url
            ORDER BY position
            """,
            (
                urls,
                REPORT_THRESHOLD,
                min_age,
                *(f"%://{host}/%" for host in excluded_hosts),
            ),
        )
        return [
            DeadLinkCandidate(
                url=str(row[0]),
                content_link_count=int(row[1]),
                role_labels=tuple(str(label) for label in (row[2] or ())),
            )
            for row in cursor.fetchall()
        ]


def record_check(*, url: str, status: str, error: str | None = None) -> int:
    """Record a probe result and immediately apply an explicit dead result.

    Discord's ``article`` embed is an explicit failure. Timeouts, rate limits,
    server errors, and missing embeds are ``unknown`` and never remove content.
    """

    if status not in {"live", "dead", "unknown"}:
        raise ValueError(f"Unsupported dead-link status: {status}")
    safe_error = error[:2000] if error else None

    with POOL.connection() as connection, connection.cursor() as cursor:
        if status == "live":
            cursor.execute(
                """
                UPDATE content_links
                SET last_checked_at = NOW(),
                    last_check_status = 'live',
                    last_check_error = NULL,
                    dead_check_failures = 0
                WHERE url = %s
                  AND is_dead = FALSE
                  AND num_reports < %s
                """,
                (url, REPORT_THRESHOLD),
            )
        elif status == "unknown":
            cursor.execute(
                """
                UPDATE content_links
                SET last_checked_at = NOW(),
                    last_check_status = 'unknown',
                    last_check_error = %s
                WHERE url = %s
                  AND is_dead = FALSE
                  AND num_reports < %s
                """,
                (safe_error, url, REPORT_THRESHOLD),
            )
        else:
            cursor.execute(
                """
                UPDATE content_links
                SET last_checked_at = NOW(),
                    last_check_status = 'dead',
                    last_check_error = %s,
                    dead_check_failures = dead_check_failures + 1
                WHERE url = %s
                  AND is_dead = FALSE
                  AND num_reports < %s
                """,
                (safe_error, url, REPORT_THRESHOLD),
            )
            if cursor.rowcount <= 0:
                return 0
            cursor.execute(
                """
                UPDATE content_links
                SET is_dead = TRUE,
                    is_recovery_exhausted = (
                        COALESCE(recovery_generation, 0) >= %s
                    )
                WHERE url = %s
                  AND is_dead = FALSE
                """,
                (CONTENT_RECOVERY_MAX_GENERATION, url),
            )
        return cursor.rowcount
