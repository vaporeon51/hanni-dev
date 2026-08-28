"""Small content lookup used by the public media resolver."""

from __future__ import annotations

from src.db import POOL


def get_live_content_url(content_link_id: int) -> str | None:
    """Return the current URL for a live feed item."""

    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT url
            FROM content_links
            WHERE content_link_id = %s
              AND is_dead = FALSE
            LIMIT 1
            """,
            (content_link_id,),
        )
        row = cursor.fetchone()
    return str(row[0]) if row else None
