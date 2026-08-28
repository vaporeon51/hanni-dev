"""Database operations for web feedback controls."""

from __future__ import annotations

from dataclasses import dataclass

from src.db import POOL


@dataclass(frozen=True)
class ContentFeedback:
    upvotes: int
    downvotes: int
    reports: int

    @property
    def score(self) -> int:
        return self.upvotes - self.downvotes


def _feedback_from_row(row) -> ContentFeedback:
    return ContentFeedback(
        upvotes=int(row[0] or 0),
        downvotes=int(row[1] or 0),
        reports=int(row[2] or 0),
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
            RETURNING num_upvotes, num_downvotes, num_reports;
            """,
            (content_link_id,),
        )
        row = cursor.fetchone()
        return _feedback_from_row(row) if row is not None else None


def add_content_report(content_link_id: int) -> ContentFeedback | None:
    """Increment the report counter for one content link."""

    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE content_links
            SET num_reports = num_reports + 1
            WHERE content_link_id = %s
            RETURNING num_upvotes, num_downvotes, num_reports;
            """,
            (content_link_id,),
        )
        row = cursor.fetchone()
        return _feedback_from_row(row) if row is not None else None
