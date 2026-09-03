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


def record_link_request(*, found: bool, cycle_reset: bool) -> int:
    """Increment one UTC daily aggregate for the random-link endpoint."""

    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO api_link_analytics_daily (
                analytics_date,
                request_count,
                success_count,
                no_result_count,
                cycle_reset_count,
                updated_at
            )
            VALUES (
                (NOW() AT TIME ZONE 'UTC')::date,
                1,
                %s,
                %s,
                %s,
                NOW()
            )
            ON CONFLICT (analytics_date)
            DO UPDATE SET
                request_count = api_link_analytics_daily.request_count + 1,
                success_count = api_link_analytics_daily.success_count + EXCLUDED.success_count,
                no_result_count = api_link_analytics_daily.no_result_count + EXCLUDED.no_result_count,
                cycle_reset_count = api_link_analytics_daily.cycle_reset_count + EXCLUDED.cycle_reset_count,
                updated_at = NOW()
            RETURNING request_count
            """,
            (int(found), int(not found), int(cycle_reset)),
        )
        return int(cursor.fetchone()[0])
