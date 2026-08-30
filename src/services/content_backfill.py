"""Pure matching rules for historical Discord content reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from src.content_ingestion import ContentLinkDraft, ROOT

DecisionAction = Literal["existing", "update", "insert", "ambiguous", "unmatched_root"]
LEGACY_CONTENT_TIMEZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ReconciliationRow:
    content_link_id: int
    role_id: str
    author_id: str | None
    uploaded_date: datetime | None
    url: str
    original_url: str | None
    source_message_id: str | None


@dataclass(frozen=True)
class ReconciliationDecision:
    action: DecisionAction
    link: ContentLinkDraft
    content_link_id: int | None = None


@dataclass(frozen=True)
class BackfillStats:
    messages_scanned: int = 0
    links_classified: int = 0
    existing_links: int = 0
    matched_legacy_links: int = 0
    inserted_links: int = 0
    ambiguous_links: int = 0
    unmatched_roots: int = 0

    def __add__(self, other: "BackfillStats") -> "BackfillStats":
        return BackfillStats(
            messages_scanned=self.messages_scanned + other.messages_scanned,
            links_classified=self.links_classified + other.links_classified,
            existing_links=self.existing_links + other.existing_links,
            matched_legacy_links=self.matched_legacy_links + other.matched_legacy_links,
            inserted_links=self.inserted_links + other.inserted_links,
            ambiguous_links=self.ambiguous_links + other.ambiguous_links,
            unmatched_roots=self.unmatched_roots + other.unmatched_roots,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "messages_scanned": self.messages_scanned,
            "links_classified": self.links_classified,
            "existing_links": self.existing_links,
            "matched_legacy_links": self.matched_legacy_links,
            "inserted_links": self.inserted_links,
            "ambiguous_links": self.ambiguous_links,
            "unmatched_roots": self.unmatched_roots,
        }


def media_identity(raw_url: str) -> tuple[str, str]:
    """Return a stable identity across common page/direct forms of one media URL."""

    parsed = urlsplit(raw_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    parts = [part for part in parsed.path.split("/") if part]
    if hostname in {"imgur.com", "i.imgur.com", "m.imgur.com"} and parts:
        if parts[0] in {"a", "gallery"} and len(parts) > 1:
            candidate = parts[1].split(".", 1)[0].rsplit("-", 1)[-1]
        else:
            candidate = parts[0].split(".", 1)[0]
        if candidate.isalnum():
            return ("imgur", candidate)

    canonical_host = hostname or parsed.netloc.lower()
    canonical_path = parsed.path.rstrip("/") or "/"
    return ("url", urlunsplit((parsed.scheme.lower(), canonical_host, canonical_path, "", "")))


def historical_naive_times(value: datetime) -> tuple[datetime, ...]:
    """Return both naive timestamp conventions found in historical rows."""

    if value.tzinfo is None:
        return (value,)
    utc_time = value.astimezone(timezone.utc).replace(tzinfo=None)
    new_york_time = value.astimezone(LEGACY_CONTENT_TIMEZONE).replace(tzinfo=None)
    return tuple(dict.fromkeys((utc_time, new_york_time)))


def _same_timestamp(left: datetime | None, right: datetime) -> bool:
    if left is None:
        return False
    return any(
        abs((left_time - right_time).total_seconds()) <= 1
        for left_time in historical_naive_times(left)
        for right_time in historical_naive_times(right)
    )


def _same_media(row: ReconciliationRow, link: ContentLinkDraft) -> bool:
    target = media_identity(link.url)
    return media_identity(row.url) == target or (
        row.original_url is not None and media_identity(row.original_url) == target
    )


def plan_reconciliation(
    links: Sequence[ContentLinkDraft],
    rows: Sequence[ReconciliationRow],
) -> list[ReconciliationDecision]:
    """Choose idempotent actions without guessing between legacy duplicates."""

    decisions: list[ReconciliationDecision] = []
    reserved_legacy_ids: set[int] = set()
    for link in links:
        existing = [
            row
            for row in rows
            if row.source_message_id == link.source_message_id
            and row.role_id == link.role_id
            and _same_media(row, link)
        ]
        if existing:
            decisions.append(ReconciliationDecision("existing", link, existing[0].content_link_id))
            continue

        candidates = [
            row
            for row in rows
            if row.source_message_id is None
            and row.content_link_id not in reserved_legacy_ids
            and row.role_id == link.role_id
            and row.author_id == link.author_id
            and _same_timestamp(row.uploaded_date, link.uploaded_date)
            and _same_media(row, link)
        ]
        if len(candidates) == 1:
            candidate = candidates[0]
            reserved_legacy_ids.add(candidate.content_link_id)
            decisions.append(ReconciliationDecision("update", link, candidate.content_link_id))
        elif len(candidates) > 1:
            decisions.append(ReconciliationDecision("ambiguous", link))
        elif link.source_kind == ROOT:
            decisions.append(ReconciliationDecision("unmatched_root", link))
        else:
            decisions.append(ReconciliationDecision("insert", link))
    return decisions


def summarize_decisions(
    decisions: Sequence[ReconciliationDecision], *, messages_scanned: int
) -> BackfillStats:
    counts = {action: 0 for action in ("existing", "update", "insert", "ambiguous", "unmatched_root")}
    for decision in decisions:
        counts[decision.action] += 1
    return BackfillStats(
        messages_scanned=messages_scanned,
        links_classified=len(decisions),
        existing_links=counts["existing"],
        matched_legacy_links=counts["update"],
        inserted_links=counts["insert"],
        ambiguous_links=counts["ambiguous"],
        unmatched_roots=counts["unmatched_root"],
    )
