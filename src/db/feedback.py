"""Database operations for web feedback controls."""

from __future__ import annotations

from dataclasses import dataclass

from src.config.constants import CONTENT_RECOVERY_MAX_GENERATION, DEAD_LINK_REPORT_THRESHOLD
from src.db import POOL


@dataclass(frozen=True)
class ContentFeedback:
    upvotes: int
    downvotes: int
    reports: int
    dead_link_reports: int = 0
    is_dead: bool = False

    @property
    def score(self) -> int:
        return self.upvotes - self.downvotes


def _feedback_from_row(row) -> ContentFeedback:
    return ContentFeedback(
        upvotes=int(row[0] or 0),
        downvotes=int(row[1] or 0),
        reports=int(row[2] or 0),
        dead_link_reports=int(row[3] or 0),
        is_dead=bool(row[4]),
    )


def add_content_vote(content_link_id: int, direction: str) -> ContentFeedback | None:
    """Increment one aggregate vote, matching the original bot behavior."""

    if direction not in {"up", "down"}:
        raise ValueError(f"Unsupported content vote direction: {direction}")

    column = "num_upvotes" if direction == "up" else "num_downvotes"
    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE content_links
            SET {column} = {column} + 1
            WHERE content_link_id = %s
            RETURNING num_upvotes, num_downvotes, num_reports, dead_link_reports, is_dead;
            """,
            (content_link_id,),
        )
        row = cursor.fetchone()
        return _feedback_from_row(row) if row is not None else None


def add_content_report(content_link_id: int, reason: str) -> ContentFeedback | None:
    """Record a wrong-idol report or a URL-wide dead-link report."""

    if reason not in {"dead_link", "wrong_idol"}:
        raise ValueError(f"Unsupported report reason: {reason}")

    with POOL.connection() as connection, connection.cursor() as cursor:
        if reason == "wrong_idol":
            cursor.execute(
                """
                UPDATE content_links
                SET num_reports = num_reports + 1
                WHERE content_link_id = %s
                RETURNING num_upvotes, num_downvotes, num_reports, dead_link_reports, is_dead;
                """,
                (content_link_id,),
            )
            row = cursor.fetchone()
            return _feedback_from_row(row) if row is not None else None

        cursor.execute(
            "SELECT url FROM content_links WHERE content_link_id = %s;",
            (content_link_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        url = str(row[0])

        # Serialize reports for the same URL so concurrent requests cannot lose
        # an increment or disagree about when the threshold was reached.
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));", (url,))
        cursor.execute(
            "SELECT COALESCE(MAX(dead_link_reports), 0) + 1 FROM content_links WHERE url = %s;",
            (url,),
        )
        dead_link_reports = int(cursor.fetchone()[0])
        cursor.execute(
            """
            UPDATE content_links
            SET dead_link_reports = %s,
                is_dead = is_dead OR %s >= %s,
                is_recovery_exhausted = CASE
                    WHEN %s >= %s
                    THEN COALESCE(recovery_generation, 0) >= %s
                    ELSE is_recovery_exhausted
                END,
                last_checked_at = CASE WHEN %s >= %s THEN NOW() ELSE last_checked_at END,
                last_check_status = CASE WHEN %s >= %s THEN 'dead' ELSE last_check_status END,
                last_check_error = CASE
                    WHEN %s >= %s THEN 'marked dead by user reports'
                    ELSE last_check_error
                END
            WHERE url = %s;
            """,
            (
                dead_link_reports,
                dead_link_reports,
                DEAD_LINK_REPORT_THRESHOLD,
                dead_link_reports,
                DEAD_LINK_REPORT_THRESHOLD,
                CONTENT_RECOVERY_MAX_GENERATION,
                dead_link_reports,
                DEAD_LINK_REPORT_THRESHOLD,
                dead_link_reports,
                DEAD_LINK_REPORT_THRESHOLD,
                dead_link_reports,
                DEAD_LINK_REPORT_THRESHOLD,
                url,
            ),
        )
        cursor.execute(
            """
            SELECT num_upvotes, num_downvotes, num_reports, dead_link_reports, is_dead
            FROM content_links
            WHERE content_link_id = %s;
            """,
            (content_link_id,),
        )
        row = cursor.fetchone()
        return _feedback_from_row(row) if row is not None else None
