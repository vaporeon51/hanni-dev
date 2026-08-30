"""Minimal aggregate storage for approximate first-party web analytics."""

from __future__ import annotations

from src.db import POOL


def record_country_session(country_code: str) -> int:
    """Increment today's UTC session count and return the new aggregate."""

    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO web_analytics_daily (
                analytics_date,
                country_code,
                session_count,
                updated_at
            )
            VALUES ((NOW() AT TIME ZONE 'UTC')::date, %s, 1, NOW())
            ON CONFLICT (analytics_date, country_code)
            DO UPDATE SET
                session_count = web_analytics_daily.session_count + 1,
                updated_at = NOW()
            RETURNING session_count
            """,
            (country_code,),
        )
        return int(cursor.fetchone()[0])
