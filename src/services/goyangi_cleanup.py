"""Discord-first cleanup using exact and per-clip media identity matches."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from src.content_dedup import (
    MediaSetIndex, candidate_posts, media_signature, visual_auto_dedupe_enabled,
)
from src.db import POOL
from src.db import content_dedup as db
from src.services.content_fingerprint import Fingerprinter


def _plan(discord, goyangi, blocked_sets, blocked_contents, *, verify_media: bool,
          limit: int, cache_writes: bool, allow_visual: bool):
    """Find duplicate clips from stored rows; never fetch Goyangi set metadata."""
    matches, unresolved = [], []
    if limit <= 0:
        return matches, unresolved

    index = MediaSetIndex(discord)
    work = [item for item in goyangi if item.id not in blocked_sets][:limit]
    candidates_by_id = {item.id: candidate_posts(item, index) for item in work}
    work = [item for item in work if candidates_by_id[item.id]]

    def compare(item):
        candidates = candidates_by_id[item.id]
        fingerprinter = Fingerprinter(cache_writes=cache_writes, seconds=45)
        found, complete = fingerprinter.match_contents(item, candidates, visual=verify_media)
        return item, candidates, found, complete

    with ThreadPoolExecutor(max_workers=min(3, len(work)) or 1) as executor:
        futures = {executor.submit(compare, item): item for item in work}
        for future in as_completed(futures):
            item = futures[future]
            try:
                _, candidates, found, complete = future.result()
            except (requests.RequestException, ValueError, KeyError, OSError) as error:
                unresolved.append({'set_id': item.id, 'reason': f'media verification failed: {type(error).__name__}'})
                continue

            for match in found:
                content_id, role_id = match['content_id'], match['role_id']
                if content_id is None or (content_id, role_id) in blocked_contents:
                    continue
                match = dict(match)
                match['set_id'] = item.id
                match['stored_signature'] = media_signature(item)
                post = next((candidate for candidate in candidates
                             if candidate.id == match['discord_root_id']), None)
                if post is None:
                    continue
                match['discord_signature'] = media_signature(post)
                if match['evidence'].get('kind') == 'video-fingerprint-v1' and not allow_visual:
                    unresolved.append({
                        'set_id': item.id,
                        'content_id': content_id,
                        'discord_root_id': match['discord_root_id'],
                        'reason': 'visual duplicate requires explicit auto-dedupe opt-in',
                    })
                    continue
                matches.append(match)

            expected = sum(len(roles) for roles in item.member_roles.values())
            found_nodes = {(match['member'], match['role_id']) for match in found}
            if not complete:
                unresolved.append({'set_id': item.id, 'reason': 'some candidate media could not be verified'})
            elif verify_media and len(found_nodes) < expected:
                # This is informational in the backfill: unmatched clips stay
                # untouched and can be reconsidered after cached assets exist.
                pass
            elif not verify_media and len(found_nodes) < expected:
                unresolved.append({'set_id': item.id, 'reason': 'visual verification disabled'})

    matches.sort(key=lambda match: (match['set_id'], match['content_id'], match['role_id']))
    unresolved.sort(key=lambda item: (item['set_id'], item.get('content_id', ''), item['reason']))
    return matches, unresolved


def cleanup(*, apply: bool, verify_media: bool = True,
            limit: int = 10000, allow_visual: bool | None = None) -> dict:
    """Archive duplicate Goyangi clips; preserve unique clips in partial sets."""
    with POOL.connection() as connection, connection.cursor() as cursor:
        discord, goyangi = db.load_sets(cursor)
        blocked_sets = db.blocked_sets(cursor)
        blocked_contents = db.blocked_contents(cursor)

    matches, unresolved = _plan(
        discord, goyangi, blocked_sets, blocked_contents,
        verify_media=verify_media, limit=limit, cache_writes=apply,
        allow_visual=allow_visual if allow_visual is not None else visual_auto_dedupe_enabled(),
    )

    deleted = 0
    if apply and matches:
        with POOL.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(db.LOCK_SQL)
            current_discord, current_goyangi = db.load_sets(cursor)
            current_posts = {post.id: post for post in current_discord}
            current_sets = {item.id: item for item in current_goyangi}
            for match in matches:
                source = current_sets.get(match['set_id'])
                post = current_posts.get(match['discord_root_id'])
                unchanged = (
                    source is not None and post is not None
                    and media_signature(source) == match['stored_signature']
                    and media_signature(post) == match['discord_signature']
                )
                if not unchanged:
                    continue
                deleted += db.remove_content(
                    cursor, match['content_id'], match['role_id'],
                    match['set_id'], match['discord_root_id'], match['evidence'],
                )

    return {
        'matches': matches,
        'unresolved': unresolved,
        'deleted': deleted,
        'deleted_contents': deleted,
        'dry_run': not apply,
        'goyangi_api_calls': 0,
    }
