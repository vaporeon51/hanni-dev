"""Second-source ingest from the public goyangi.pics PocketBase API.

Embed-only policy: gif filetype, direct/imgur origin, cdn preview URLs.
Discord-origin contents are always skipped (dupes or attachment posts).
Idempotent: unique index on (goyangi_content_id, role_id) plus exact-URL
and mirror imgur-ID pre-checks. Cursor lives in goyangi_ingest_state and
never touches update_log.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import requests

from src.db import POOL

API_BASE = "https://api.goyangi.pics/api/collections/contents_sets/records"
PAGE_DELAY_SECONDS = 1.2
# Re-scan this far behind the stored cursor each pass: catches late-edited
# sets while keeping every incremental run to ~1 API page. Repeats are
# harmless (unique index + content-ID pre-checks).
CURSOR_OVERLAP = timedelta(hours=24)

# Group codes seen in the wild -> normalized role_info group names.
# Only non-obvious mappings live here; plain lowercase matches need nothing.
GROUP_ALIASES = {
    "idle": "gidle",
}

# A 'solo_' prefix means "member acting solo": match the member name only,
# and accept it solely when exactly one role carries that member name.
SOLO_GROUP_CODE = "solo"

INSERT_GOYANGI_LINK = """
    INSERT INTO content_links
        (role_id, author_id, author, uploaded_date, url, initial_reaction_count,
         num_upvotes, num_reports, processed_date, is_dead, source_kind,
         goyangi_content_id, goyangi_set_id)
    VALUES
        (%s, %s, %s, %s, %s, 0, 0, 0, NOW(), FALSE, 'goyangi', %s, %s)
    ON CONFLICT (goyangi_content_id, role_id)
        WHERE goyangi_content_id IS NOT NULL DO NOTHING;
"""


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _expand_list(node: dict, key: str) -> list[dict]:
    value = (node.get("expand") or {}).get(key, [])
    if isinstance(value, dict):
        return [value]
    return [item for item in value if isinstance(item, dict)]


def build_role_lookup() -> tuple[dict[tuple[str, str], str], set[str]]:
    """Return ({(norm_member, norm_group): role_id}, {norm_group})."""

    lookup: dict[tuple[str, str], str] = {}
    groups: set[str] = set()
    with POOL.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT role_id, member_name, group_name FROM role_info;")
            for role_id, member_name, group_name in cursor.fetchall():
                member = _norm(str(member_name or ""))
                group = _norm(str(group_name or ""))
                groups.add(group)
                lookup[(member, group)] = str(role_id)
    return lookup, groups


def resolve_role(code: str, lookup: dict[tuple[str, str], str], groups: set[str]) -> str | None:
    """Map a goyangi idol code (group_member) to a role_id, or None."""

    code = code.strip().lower()
    if "_" not in code:
        return None
    parts = code.split("_")
    for split in range(len(parts) - 1, 0, -1):
        group_code = _norm("_".join(parts[:split]))
        group_code = GROUP_ALIASES.get(group_code, group_code)
        member_code = _norm("_".join(parts[split:]))
        if group_code in groups and (member_code, group_code) in lookup:
            return lookup[(member_code, group_code)]
    # The longest-prefix loop above already tried every group split; only the
    # solo special-case remains.
    if _norm(parts[0]) == SOLO_GROUP_CODE:
        member_code = _norm("_".join(parts[1:]))
        candidates = [role_id for (member, _), role_id in lookup.items() if member == member_code]
        return candidates[0] if len(candidates) == 1 else None
    return None


def _imgur_ids(url: str) -> set[str]:
    host = (urlsplit(url).hostname or "").lower()
    if "imgur.com" not in host and "imgur.gg" not in host:
        return set()
    ids: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9]{5,}", urlsplit(url).path):
        if token.lower() not in {"gallery", "album", "a"}:
            ids.add(token)
    return ids


def load_dedup_state() -> tuple[set[str], set[str], set[str]]:
    """Return (exact_urls, goyangi_content_ids, imgur_ids) from content_links."""

    exact: set[str] = set()
    goyangi_ids: set[str] = set()
    imgur_ids: set[str] = set()
    with POOL.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT url, goyangi_content_id FROM content_links;")
            for url, content_id in cursor.fetchall():
                if isinstance(url, str) and url:
                    exact.add(url)
                    imgur_ids.update(_imgur_ids(url))
                if content_id:
                    goyangi_ids.add(str(content_id))
    return exact, goyangi_ids, imgur_ids


def parse_created(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch_sets(since: str | None, per_page: int, max_sets: int,
               origins: tuple[str, ...] = ("direct", "imgur")) -> list[dict]:
    """Page newest-first through contents_sets with the needed expands."""

    out: list[dict] = []
    page = 1
    session = requests.Session()
    origin_filter = " || ".join(f'(origin="{origin}")' for origin in origins)
    while len(out) < max_sets:
        filt = f"({origin_filter}) && (contents_via_set.id ?!= \"\")"
        if since:
            filt = f'created >= "{since}" && {filt}'
        params = {
            "page": page,
            "perPage": per_page,
            "sort": "-created",
            "filter": filt,
            "expand": "idol,group,uploader,contents_via_set,contents_via_set.idol,"
            "contents_via_set.group,contents_via_set.uploader",
        }
        response = session.get(API_BASE, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", [])
        if not items:
            break
        out.extend(items)
        if page >= int(payload.get("totalPages", page)):
            break
        page += 1
        time.sleep(PAGE_DELAY_SECONDS)
    return out[:max_sets]


def iter_contents(item: dict) -> tuple[dict[str, str], dict[str, str], list[dict]]:
    """Return (idol_codes, uploaders, contents) for one set record."""

    idol_codes = {e.get("id"): e.get("code", "") for e in _expand_list(item, "idol")}
    uploaders = {e.get("id"): e.get("name", "") for e in _expand_list(item, "uploader")}
    contents = _expand_list(item, "contents_via_set")
    for content in contents:
        for e in _expand_list(content, "idol"):
            idol_codes.setdefault(e.get("id"), e.get("code", ""))
        for e in _expand_list(content, "uploader"):
            uploaders.setdefault(e.get("id"), e.get("name", ""))
    return idol_codes, uploaders, contents


def decide(content: dict, idol_codes: dict[str, str], lookup, groups,
           exact: set[str], goyangi_ids: set[str], imgur_ids: set[str]
           ) -> tuple[str, list[str], str]:
    """Decide one content record; resolve roles exactly once.

    Returns (decision, roles, detail). ``roles`` is non-empty only for
    ``would-insert``, so callers never re-resolve.
    """

    cid = str(content.get("id") or "")
    if content.get("filetype") != "gif":
        return ("skip:filetype", [], str(content.get("filetype")))
    if content.get("origin") == "discord":
        return ("skip:discord-origin", [], "")
    if content.get("discord"):
        return ("skip:has-discord-link", [], str(content.get("discord")))
    if cid in goyangi_ids:
        return ("skip:content-id-dupe", [], cid)
    preview = str(content.get("preview") or "")
    if preview and preview in exact:
        return ("skip:url-dupe", [], preview)
    mirror = str(content.get("mirror") or "")
    if mirror and (_imgur_ids(mirror) & imgur_ids):
        return ("skip:mirror-dupe", [], mirror)
    roles: list[str] = []
    for idol_id in content.get("idol") or []:
        code = idol_codes.get(str(idol_id), "")
        role_id = resolve_role(code, lookup, groups) if code else None
        if role_id is None:
            return ("skip:unmapped-idol", [], code or str(idol_id))
        roles.append(role_id)
    if not roles:
        return ("skip:unmapped-idol", [], "(no idol)")
    if not preview:
        return ("skip:no-preview", [], cid)
    return ("would-insert", roles, "")


def _draft_rows(content: dict, roles: list[str], uploaders: dict[str, str],
                set_id: str) -> list[tuple]:
    created = parse_created(content.get("created"))
    if created is None:
        return []
    uploader_id = str(content.get("uploader") or "")
    author = uploaders.get(uploader_id, uploader_id)
    return [
        (role_id, uploader_id, author, created, str(content.get("preview")),
         str(content.get("id")), set_id)
        for role_id in roles
    ]


def read_cursor() -> tuple[datetime | None, str | None]:
    """Return (last_content_created, last_content_id) or (None, None)."""

    with POOL.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT last_content_created, last_content_id FROM goyangi_ingest_state WHERE id = 1;")
            row = cursor.fetchone()
    if row is None:
        return None, None
    return row[0], (str(row[1]) if row[1] is not None else None)


def run_pass(*, since: str | None, per_page: int, max_sets: int, apply: bool) -> dict:
    """Fetch, decide, and optionally insert one pass. Returns a summary.

    ``since=None`` resumes from the stored cursor minus a small overlap, so
    scheduled runs only ever scan ~1 page. Pass an explicit ``since`` for
    backfills. The cursor only ever moves forward.
    """

    if since is None:
        cursor_created, _ = read_cursor()
        if cursor_created is not None:
            since = (cursor_created - CURSOR_OVERLAP).strftime("%Y-%m-%d %H:%M:%S")

    lookup, groups = build_role_lookup()
    exact, goyangi_ids, imgur_ids = load_dedup_state()
    sets = fetch_sets(since, per_page, max_sets)
    summary: dict[str, Any] = {"sets": len(sets), "contents": 0, "inserted": 0, "skipped": 0}
    decisions: dict[str, int] = {}
    high_watermark: tuple[datetime, str] | None = None

    with POOL.connection() as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                for item in sets:
                    idol_codes, uploaders, contents = iter_contents(item)
                    for content in contents:
                        summary["contents"] += 1
                        created = parse_created(content.get("created"))
                        cid = str(content.get("id") or "")
                        if created is not None and (
                            high_watermark is None or (created, cid) > high_watermark
                        ):
                            high_watermark = (created, cid)
                        decision, roles, _ = decide(content, idol_codes, lookup, groups,
                                                    exact, goyangi_ids, imgur_ids)
                        decisions[decision] = decisions.get(decision, 0) + 1
                        if decision != "would-insert":
                            summary["skipped"] += 1
                            continue
                        rows = _draft_rows(content, roles, uploaders,
                                           str(item.get("id") or ""))
                        for row in rows:
                            if apply:
                                cursor.execute(INSERT_GOYANGI_LINK, row)
                                summary["inserted"] += cursor.rowcount
                            else:
                                summary["inserted"] += 1
                            # In-memory guard so one pass never double-inserts.
                            goyangi_ids.add(str(content.get("id")))
                            exact.add(str(content.get("preview")))
                if apply and high_watermark is not None:
                    cursor.execute(
                        """
                        UPDATE goyangi_ingest_state
                        SET last_content_created = %s, last_content_id = %s,
                            updated_at = NOW()
                        WHERE id = 1
                          AND (
                              last_content_created IS NULL
                              OR last_content_created < %s
                              OR (last_content_created = %s AND (last_content_id IS NULL OR last_content_id < %s))
                          );
                        """,
                        (*high_watermark, high_watermark[0], high_watermark[0], high_watermark[1]),
                    )
    summary["decisions"] = decisions
    summary["high_watermark"] = (
        (high_watermark[0].isoformat(), high_watermark[1]) if high_watermark else None
    )
    return summary


def sweep_forward_race(*, apply: bool, limit: int = 500) -> dict:
    """Find Discord-ingested viewer links duplicating goyangi-sourced rows.

    Keeps the goyangi row (stable cdn URL + provenance) and reports, or with
    apply=True deletes, the newer Discord-side viewer-link row.
    """

    with POOL.connection() as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT content_link_id, url, uploaded_date,
                           split_part(substring(url from '/v/([^/?#]+)'), '.', 1)
                               AS viewer_id
                    FROM content_links
                    WHERE url LIKE '%%goyangi.pics/v/%%'
                      AND source_message_id IS NOT NULL
                    LIMIT %s;
                    """,
                    (limit,),
                )
                viewer_rows = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT content_link_id, goyangi_content_id, url, uploaded_date
                    FROM content_links
                    WHERE source_kind = 'goyangi' AND goyangi_content_id IS NOT NULL;
                    """
                )
                keep = {str(row[1]): row for row in cursor.fetchall()}
                pairs: list[tuple] = []
                for link_id, url, uploaded, viewer_id in viewer_rows:
                    if viewer_id and viewer_id in keep:
                        pairs.append((link_id, url, uploaded, keep[viewer_id]))
                deleted = 0
                if apply:
                    for link_id, _, _, _ in pairs:
                        cursor.execute(
                            "DELETE FROM content_links WHERE content_link_id = %s;",
                            (link_id,),
                        )
                        deleted += cursor.rowcount
    return {"candidates": len(pairs), "deleted": deleted,
            "pairs": [(p[1], p[3][2]) for p in pairs[:10]]}
