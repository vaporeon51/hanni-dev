"""Small content lookup used by the public media resolver."""

from __future__ import annotations

from src.config.constants import EPHEMERAL_MEDIA_HOSTS
from src.db import POOL


def get_live_content_url(content_link_id: int) -> str | None:
    """Return the current URL for a live feed item."""

    excluded_hosts = sorted(EPHEMERAL_MEDIA_HOSTS)
    excluded_sql = "\n              " + "\n              ".join(
        "AND LOWER(url) NOT LIKE %s" for _host in excluded_hosts
    )
    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT url
            FROM content_links
            WHERE content_link_id = %s
              AND is_dead = FALSE
              {excluded_sql}
            LIMIT 1
            """,
            (content_link_id, *(f"%://{host}/%" for host in excluded_hosts)),
        )
        row = cursor.fetchone()
    return str(row[0]) if row else None
