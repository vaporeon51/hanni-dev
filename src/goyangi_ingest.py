"""Second-source ingest from the public goyangi.pics PocketBase API.

Embed-only policy: gif filetype, direct/imgur origin, cdn preview URLs.
Discord-origin contents are always skipped (dupes or attachment posts).
Idempotent: unique index on (goyangi_content_id, role_id) plus exact-URL
and mirror imgur-ID pre-checks. Cursor lives in goyangi_ingest_state and
never touches update_log. Fingerprint-only set suppression stays pending until
GOYANGI_ALLOW_VISUAL_AUTO_DEDUPE=1 explicitly enables it.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

from src.db import POOL
from src.content_dedup import (
    MediaSet, MediaSetIndex, media_key, exact_match, candidate_posts, media_signature,
    visual_auto_dedupe_enabled, VISUAL_REVIEW_REASON,
)
from src.db import content_dedup as dedup_db
from src.services.content_fingerprint import Fingerprinter, Unverified

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
         goyangi_content_id, goyangi_set_id, original_url, mirror_url)
    VALUES
        (%s, %s, %s, %s, %s, 0, 0, 0, NOW(), FALSE, 'goyangi', %s, %s, %s, %s)
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
    key = media_key(url)
    return {key} if key.startswith("imgur:") else set()


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


def fetch_set(set_id: str) -> dict:
    """Fetch one older set queued for a delayed cross-source recheck."""
    response = requests.get(
        f"{API_BASE}/{set_id}",
        params={"expand": "idol,group,uploader,contents_via_set,contents_via_set.idol,"
                           "contents_via_set.group,contents_via_set.uploader"},
        timeout=10,
    )
    response.raise_for_status()
    item = response.json()
    if not isinstance(item.get("expand", {}).get("contents_via_set"), (list, dict)):
        raise ValueError("Goyangi set response omitted expanded contents")
    return item


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
    preview = str(content.get("preview") or "")
    original = str(content.get("original") or "") or None
    mirror = str(content.get("mirror") or "") or None
    return [
        (role_id, uploader_id, author, created, preview,
         str(content.get("id")), set_id, original, mirror)
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


def _set_media(item, lookup, groups):
    media = MediaSet(str(item['id']), complete=False)
    codes, _, contents = iter_contents(item)
    expanded = (item.get('expand') or {}).get('contents_via_set')
    complete = isinstance(expanded, (list, dict)) and bool(contents)
    for content in contents:
        if content.get('filetype') != 'gif':
            continue
        idols = content.get('idol') or []
        content_roles = [
            (idol, resolve_role(codes.get(str(idol), ''), lookup, groups))
            for idol in idols
        ]
        if not content_roles or not content.get('preview') or not content.get('id'):
            complete = False
            continue
        if any(role is None for _, role in content_roles):
            complete = False
        for idol, role in content_roles:
            if role:
                media.add(role, content['preview'], parse_created(content.get('created')),
                          content.get('id'), content.get('original'), content.get('mirror'))
    media.complete = complete and bool(media.members)
    return media


def _signature(posts):
    return tuple(sorted((p.id, media_signature(p)) for p in posts))


def _full_member_match(media, member_matches, candidates):
    """Return a same-Discord-set match only when every source member is covered."""
    if not media.complete:
        return None
    expected = {
        (member, role)
        for member, roles in media.member_roles.items()
        for role in roles
    }
    matched = {(match['member'], match['role_id']) for match in member_matches}
    roots = {match['discord_root_id'] for match in member_matches}
    if not expected or matched != expected or len(roots) != 1:
        return None
    post = next((candidate for candidate in candidates if candidate.id in roots), None)
    if post is None:
        return None
    kind = ('shared-media' if all(match['evidence']['kind'] == 'shared-media'
                                  for match in member_matches)
            else 'video-fingerprint-v1')
    return post, {'kind': kind, 'clips': len(expected)}


def run_pass(*, since: str | None, per_page: int, max_sets: int, apply: bool) -> dict:
    """Discord-first ingest. Unverified candidate sets survive cursor advances.

    Fetch/decoding happens outside write transactions. The final check and
    mutations share a transaction lock with Discord ingestion and cleanup.
    """
    if since is None:
        cursor_created, _ = read_cursor()
        if cursor_created is not None:
            since = (cursor_created - CURSOR_OVERLAP).strftime("%Y-%m-%d %H:%M:%S")
    lookup, groups = build_role_lookup()
    fetched = fetch_sets(since, per_page, max_sets)
    with POOL.connection() as connection, connection.cursor() as cursor:
        discord, existing = dedup_db.load_sets(cursor)
        blocked = dedup_db.blocked_sets(cursor)
        blocked_contents = dedup_db.blocked_contents(cursor)
        cursor.execute("SELECT payload FROM goyangi_pending_sets WHERE retry_after <= NOW() ORDER BY random() LIMIT %s", (max_sets,))
        pending = [r[0] for r in cursor.fetchall()]
        cursor.execute("SELECT set_id FROM goyangi_pending_sets WHERE retry_after > NOW()")
        cooling = {r[0] for r in cursor.fetchall()}
    # Fresh metadata takes precedence over a stored retry payload. Existing
    # pre-migration rows carry no Goyangi mirror metadata, so hydrate a bounded
    # number of those sets by their stable API ID before media verification.
    items = {item['id']: item for item in pending}
    items.update({item['id']: item for item in fetched if item['id'] not in cooling})
    hydrate_errors = {}
    hydrate = [(sid, item) for sid, item in items.items() if item.get('existing_only')][:25]
    if hydrate:
        with ThreadPoolExecutor(max_workers=min(5, len(hydrate))) as executor:
            futures = {executor.submit(fetch_set, sid): sid for sid, _ in hydrate}
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    items[sid] = future.result()
                except (requests.RequestException, ValueError) as error:
                    hydrate_errors[sid] = type(error).__name__
    existing = {item.id: item for item in existing}
    discord_index = MediaSetIndex(discord)
    plans = []
    high_watermark = None
    for item in fetched:
        for content in iter_contents(item)[2]:
            created = parse_created(content.get('created'))
            if created:
                point = (created, str(content.get('id') or ''))
                high_watermark = max(high_watermark, point) if high_watermark else point
    for sid, item in items.items():
        if sid in blocked:
            plans.append((item, None, None, [], 'blocked', []))
            continue
        media = existing.get(sid) if item.get('existing_only') else _set_media(item, lookup, groups)
        if sid in hydrate_errors:
            plans.append((item, media, None, [], 'Goyangi metadata unavailable: ' + hydrate_errors[sid], []))
            continue
        if media is None or not media.urls:
            plans.append((item, media, None, [], 'empty', []))
            continue
        candidates = candidate_posts(media, discord_index)
        reason = None
        member_matches = []
        verification_complete = True
        if candidates:
            try:
                # Per-set budget: the old shared 45s deadline expired mid-pass
                # and deferred every later set. Album expansions are now shared
                # via imgur_album_cache, so per-set instances stay cheap.
                member_matches, verification_complete = Fingerprinter(
                    cache_writes=apply, seconds=15,
                ).match_contents(
                    media, candidates, visual=True, excluded=blocked_contents,
                )
            except Unverified as error:
                reason = str(error)
        match = _full_member_match(media, member_matches, candidates)
        has_visual_match = any(
            matched['evidence'].get('kind') == 'video-fingerprint-v1'
            for matched in member_matches
        )
        if has_visual_match and not visual_auto_dedupe_enabled():
            match, member_matches = None, []
            reason = VISUAL_REVIEW_REASON
        elif not verification_complete:
            reason = 'some candidate media could not be verified'
        if not media.complete and item.get('existing_only'):
            reason = reason or 'complete Goyangi set metadata unavailable'
        if not match and not reason and media.dates and (
            datetime.now(timezone.utc) - max(media.dates) < timedelta(minutes=15)
        ):
            reason = 'waiting 15 minutes for Discord'
        plans.append((item, media, match, member_matches, reason, _signature(candidates)))
    summary = {'sets': len(items), 'contents': 0, 'inserted': 0, 'skipped': 0,
               'duplicate_contents': 0,
               'deleted': 0, 'duplicate_sets': 0, 'deferred_sets': 0, 'decisions': {}}
    with POOL.connection() as connection, connection.transaction(), connection.cursor() as cursor:
        cursor.execute(dedup_db.LOCK_SQL)
        fresh_discord, _ = dedup_db.load_sets(cursor)
        fresh_index = MediaSetIndex(fresh_discord)
        blocked = dedup_db.blocked_sets(cursor)
        blocked_contents = dedup_db.blocked_contents(cursor)
        # Load under the same lock, using this connection (pool may have size 1).
        cursor.execute('SELECT url, goyangi_content_id FROM content_links')
        rows = cursor.fetchall()
        exact = {r[0] for r in rows}
        ids = {str(r[1]) for r in rows if r[1]}
        imgur_ids = {key for url in exact for key in _imgur_ids(url)}
        for item, media, match, member_matches, reason, signature in plans:
            sid = item['id']
            codes, uploaders, contents = iter_contents(item)
            summary['contents'] += len(contents)
            if sid in blocked:
                summary['skipped'] += len(contents)
                summary['duplicate_sets'] += 1
                continue
            if media:
                # Exact matches have priority, including Discord arrivals while
                # we were downloading. Never act on stale visual evidence.
                fresh_candidates = candidate_posts(media, fresh_index)
                fresh_match = exact_match(media, fresh_index)
                if fresh_match:
                    match, reason = fresh_match, None
                    member_matches = []
                elif _signature(fresh_candidates) != signature:
                    match, member_matches, reason = None, [], 'Discord changed during verification'
            if match:
                post, evidence = match
                if apply:
                    summary['deleted'] += dedup_db.remove_set(cursor, sid, post.id, evidence)
                summary['duplicate_sets'] += 1
                summary['skipped'] += len(contents)
                continue
            planned_duplicates = set()
            for duplicate in member_matches:
                content_id, role_id = duplicate['content_id'], duplicate['role_id']
                if content_id is None:
                    continue
                planned_duplicates.add((content_id, role_id))
                summary['duplicate_contents'] += 1
                if apply:
                    summary['deleted'] += dedup_db.remove_content(
                        cursor, content_id, role_id, sid,
                        duplicate['discord_root_id'], duplicate['evidence'],
                    )
                    blocked_contents.add((content_id, role_id))
            if reason and reason != 'empty':
                if apply:
                    dedup_db.defer_set(cursor, item, reason)
                summary['deferred_sets'] += 1
                summary['skipped'] += len(contents)
                continue
            for content in contents:
                decision, roles, _ = decide(content, codes, lookup, groups, exact, ids, imgur_ids)
                if decision == 'would-insert':
                    content_id = str(content.get('id') or '')
                    duplicate_roles = [role for role in roles
                                       if ((content_id, role) in blocked_contents
                                           or (content_id, role) in planned_duplicates)]
                    if duplicate_roles:
                        roles = [role for role in roles if role not in duplicate_roles]
                        if not roles:
                            decision = 'skip:confirmed-duplicate-content'
                summary['decisions'][decision] = summary['decisions'].get(decision, 0) + 1
                if decision != 'would-insert':
                    summary['skipped'] += 1
                    continue
                for row in _draft_rows(content, roles, uploaders, sid):
                    if apply:
                        cursor.execute(INSERT_GOYANGI_LINK, row)
                        summary['inserted'] += cursor.rowcount
                    else:
                        summary['inserted'] += 1
                ids.add(str(content.get('id')))
                exact.add(str(content.get('preview')))
            if apply:
                # Discord writes enqueue only likely candidates. Keeping every
                # successfully ingested set here would create an endless queue.
                cursor.execute('DELETE FROM goyangi_pending_sets WHERE set_id=%s', (sid,))
        if apply and high_watermark:
            cursor.execute("""UPDATE goyangi_ingest_state
                SET last_content_created=%s,last_content_id=%s,updated_at=NOW()
                WHERE id=1 AND (last_content_created IS NULL OR
                  (last_content_created, COALESCE(last_content_id,'')) < (%s,%s))""",
                (*high_watermark, *high_watermark))
    summary['high_watermark'] = (high_watermark[0].isoformat(), high_watermark[1]) if high_watermark else None
    return summary


def sweep_forward_race(*, apply: bool, limit: int = 500) -> dict:
    """Compatibility entrypoint: now ALWAYS removes Goyangi, never Discord."""
    from src.services.goyangi_cleanup import cleanup
    result = cleanup(apply=apply, verify_media=False, limit=limit)
    return {'candidates': len(result['matches']), 'deleted': result['deleted'],
            'pairs': [(m['set_id'], m['discord_root_id']) for m in result['matches']]}
