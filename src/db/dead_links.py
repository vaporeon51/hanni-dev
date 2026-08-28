"""Persistent state for Discord-based dead-link checks."""

from __future__ import annotations

from dataclasses import dataclass

from src.config.constants import (
    CONTENT_RECOVERY_MAX_GENERATION,
    DEAD_LINK_MAX_FAILURES,
    EPHEMERAL_MEDIA_HOSTS,
    MIN_CONTENT_AGE,
    REPORT_THRESHOLD,
)
from src.db import POOL


@dataclass(frozen=True)
class DeadLinkCandidate:
    url: str
    content_link_count: int


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
            SELECT cl.url, COUNT(*)::integer AS content_link_count
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
            GROUP BY cl.url
            ORDER BY MIN(cl.last_checked_at) NULLS FIRST, cl.url
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
        return [DeadLinkCandidate(url=str(row[0]), content_link_count=int(row[1])) for row in cursor.fetchall()]


def record_check(*, url: str, status: str, error: str | None = None) -> int:
    """Record a probe result and transition a URL to dead after confirmation.

    Only repeated, explicit dead responses mark content dead. Timeouts, rate
    limits, and server errors are stored as ``unknown`` by the worker and never
    remove content from the feed.
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
            cursor.execute(
                """
                UPDATE content_links
                SET is_dead = TRUE,
                    is_recovery_exhausted = (
                        COALESCE(recovery_generation, 0) >= %s
                    )
                WHERE url = %s
                  AND is_dead = FALSE
                  AND dead_check_failures >= %s
                """,
                (CONTENT_RECOVERY_MAX_GENERATION, url, DEAD_LINK_MAX_FAILURES),
            )
        return cursor.rowcount
