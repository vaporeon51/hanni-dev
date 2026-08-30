"""Postgres persistence for the one-time historical content backfill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import psycopg

from src.content_ingestion import ContentLinkDraft
from src.services.content_backfill import (
    BackfillStats,
    ReconciliationDecision,
    ReconciliationRow,
    historical_naive_times,
    plan_reconciliation,
    summarize_decisions,
)

from . import POOL
from .content_update import INSERT_CONTENT_LINK, content_link_params

DISCORD_EPOCH_MILLISECONDS = 1_420_070_400_000
DEFAULT_BACKFILL_START_UTC = datetime(2025, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class BackfillBounds:
    start_message_id: str
    end_message_id: str


def datetime_to_discord_snowflake(value: datetime) -> str:
    """Build a lower-bound snowflake from a UTC timestamp."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    milliseconds = int(value.timestamp() * 1000)
    return str(max(0, milliseconds - DISCORD_EPOCH_MILLISECONDS) << 22)


def get_default_bounds() -> BackfillBounds | None:
    """Replay from 2025, with two minutes of leading classifier context."""

    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT last_message_id
            FROM update_log
            ORDER BY processed_date DESC
            LIMIT 1;
            """
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Cannot determine a historical backfill end message")
        live_cursor = str(row[0])

    start = datetime_to_discord_snowflake(DEFAULT_BACKFILL_START_UTC - timedelta(minutes=2))
    if int(start) >= int(live_cursor):
        return None
    return BackfillBounds(start_message_id=start, end_message_id=live_cursor)


def _candidate_rows(cursor: psycopg.Cursor[Any], links: Sequence[ContentLinkDraft]) -> list[ReconciliationRow]:
    if not links:
        return []

    source_ids = list(dict.fromkeys(link.source_message_id for link in links))
    role_ids = list(dict.fromkeys(link.role_id for link in links))
    cursor.execute(
        """
        SELECT content_link_id, role_id, author_id, uploaded_date, url, original_url,
               source_message_id
        FROM content_links
        WHERE source_message_id = ANY(%s)
          AND role_id = ANY(%s);
        """,
        (source_ids, role_ids),
    )
    rows = list(cursor.fetchall())

    unique_targets = list(
        dict.fromkeys(
            (link.role_id, link.author_id, timestamp)
            for link in links
            for timestamp in historical_naive_times(link.uploaded_date)
        )
    )
    cursor.execute(
        """
        WITH targets(role_id, author_id, uploaded_date) AS (
            SELECT *
            FROM UNNEST(%s::text[], %s::text[], %s::timestamp[])
        )
        SELECT DISTINCT cl.content_link_id, cl.role_id, cl.author_id, cl.uploaded_date,
               cl.url, cl.original_url, cl.source_message_id
        FROM content_links AS cl
        JOIN targets AS target
          ON target.role_id = cl.role_id
         AND target.author_id = cl.author_id
         AND cl.uploaded_date BETWEEN target.uploaded_date - INTERVAL '1 second'
                                  AND target.uploaded_date + INTERVAL '1 second'
        WHERE cl.source_message_id IS NULL;
        """,
        (
            [target[0] for target in unique_targets],
            [target[1] for target in unique_targets],
            [target[2] for target in unique_targets],
        ),
    )
    rows.extend(cursor.fetchall())

    seen_ids: set[int] = set()
    result: list[ReconciliationRow] = []
    for row in rows:
        content_link_id = int(row[0])
        if content_link_id in seen_ids:
            continue
        seen_ids.add(content_link_id)
        result.append(
            ReconciliationRow(
                content_link_id=content_link_id,
                role_id=str(row[1]),
                author_id=str(row[2]) if row[2] is not None else None,
                uploaded_date=row[3],
                url=str(row[4]),
                original_url=str(row[5]) if row[5] is not None else None,
                source_message_id=str(row[6]) if row[6] is not None else None,
            )
        )
    return result


def _apply_decisions(
    cursor: psycopg.Cursor[Any],
    decisions: Sequence[ReconciliationDecision],
    processed_date: datetime,
) -> None:
    updates = [decision for decision in decisions if decision.action == "update"]
    if updates:
        cursor.executemany(
            """
            UPDATE content_links
            SET source_message_id = %s,
                root_message_id = %s,
                source_kind = %s
            WHERE content_link_id = %s
              AND source_message_id IS NULL;
            """,
            (
                (
                    decision.link.source_message_id,
                    decision.link.root_message_id,
                    decision.link.source_kind,
                    decision.content_link_id,
                )
                for decision in updates
            ),
        )

    inserts = [decision.link for decision in decisions if decision.action == "insert"]
    if inserts:
        cursor.executemany(
            INSERT_CONTENT_LINK,
            (content_link_params(link, processed_date) for link in inserts),
        )


def reconcile_page(
    links: Sequence[ContentLinkDraft],
    *,
    messages_scanned: int,
    apply: bool,
    verbose: bool = False,
) -> BackfillStats:
    """Plan one page and optionally commit its reconciled content rows."""

    with POOL.connection() as connection, connection.transaction(), connection.cursor() as cursor:
        decisions = plan_reconciliation(links, _candidate_rows(cursor, links))
        stats = summarize_decisions(decisions, messages_scanned=messages_scanned)
        if verbose:
            noteworthy = [
                decision
                for decision in decisions
                if decision.action in {"insert", "ambiguous", "unmatched_root"}
            ]
            for decision in noteworthy[:20]:
                print(
                    f"  {decision.action}: message={decision.link.source_message_id} "
                    f"role={decision.link.role_id} url={decision.link.url}",
                    flush=True,
                )
            if len(noteworthy) > 20:
                print(f"  ... {len(noteworthy) - 20:,} more noteworthy decisions", flush=True)
        if not apply:
            return stats

        _apply_decisions(cursor, decisions, datetime.now(timezone.utc))
        return stats
