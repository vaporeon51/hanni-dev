"""One-time Discord-first cleanup for the supplied confirmed duplicate sets."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from src.content_dedup import (
    MediaSetIndex, exact_match, candidate_posts, media_signature,
    visual_auto_dedupe_enabled, VISUAL_REVIEW_REASON,
)
from src.db import POOL
from src.db import content_dedup as db
from src.services.content_fingerprint import Fingerprinter, Unverified

# Independently confirmed by the requester. IDs are the actual Discord root
# messages and Goyangi set IDs observed in the database on 2026-09-27.
CONFIRMED_PAIRS = {
    '260927-itzy-yuna-29bd7332': '1553802678516322504',
    '260927-babymonster-asa-e8367093': '1553633766482903100',
    '260925-ive-wonyoung-1c8597c6': '1553160169381175436',
    '260920-aespa-karina-winter-7d5ed718': '1553133933040767167',
    '260925-blackpink-lisa-a6937834': '1553110809205539038',
    '260925-aespa-ningning-5ab7a331': '1553075486857367642',
}


def _plan(discord, goyangi, blocked, *, confirmed: bool, verify_media: bool,
          limit: int, cache_writes: bool, allow_visual: bool):
    discord_by_id = {post.id: post for post in discord}
    discord_index = MediaSetIndex(discord)
    fp = Fingerprinter(cache_writes=cache_writes, seconds=90) if verify_media else None
    matches, unresolved = [], []
    if limit <= 0:
        return matches, unresolved

    work = [item for item in goyangi if item.id not in blocked][:limit]
    shortlist = {item.id: candidate_posts(item, discord_index) for item in work}
    needs_hydration = (
        [item for item in work if not item.complete and shortlist[item.id]]
        if verify_media else []
    )
    hydrated, hydration_errors = {}, {}
    if needs_hydration:
        from src.goyangi_ingest import build_role_lookup, fetch_set, _set_media
        role_lookup = build_role_lookup()
        with ThreadPoolExecutor(max_workers=min(5, len(needs_hydration))) as executor:
            futures = {executor.submit(fetch_set, item.id): item for item in needs_hydration}
            for future in as_completed(futures):
                stored_item = futures[future]
                try:
                    media = _set_media(future.result(), *role_lookup)
                    if not media.complete:
                        raise Unverified('Goyangi set expansion is incomplete')
                    hydrated[stored_item.id] = media
                except (requests.RequestException, ValueError, KeyError, Unverified) as error:
                    hydration_errors[stored_item.id] = type(error).__name__

    for stored_item in work:
        item = hydrated.get(stored_item.id, stored_item)
        if stored_item.id in hydration_errors:
            unresolved.append({'set_id': stored_item.id,
                               'reason': f'Goyangi metadata unavailable: {hydration_errors[stored_item.id]}'})
            continue
        if confirmed and item.id in CONFIRMED_PAIRS:
            root = CONFIRMED_PAIRS[item.id]
            post = discord_by_id.get(root)
            if post is None:
                unresolved.append({'set_id': item.id, 'reason': f'confirmed Discord root {root} not present'})
                continue
            matches.append({'set_id': item.id, 'discord_root_id': root,
                            'evidence': {'kind': 'user-confirmed-pair'},
                            'stored_signature': media_signature(stored_item),
                            'discord_signature': media_signature(post)})
            continue
        match = exact_match(item, discord_index)
        candidates = candidate_posts(item, discord_index)
        if match:
            post, evidence = match
            matches.append({'set_id': item.id, 'discord_root_id': post.id, 'evidence': evidence,
                            'stored_signature': media_signature(stored_item),
                            'discord_signature': media_signature(post)})
            continue
        if candidates and not verify_media:
            unresolved.append({'set_id': item.id, 'reason': 'API and video verification disabled'})
        if verify_media and fp and candidates:
            try:
                match = fp.match(item, candidates)
            except Unverified as error:
                unresolved.append({'set_id': item.id, 'reason': str(error)})
            else:
                if match:
                    post, evidence = match
                    if evidence.get('kind') == 'video-fingerprint-v1' and not allow_visual:
                        unresolved.append({
                            'set_id': item.id,
                            'discord_root_id': post.id,
                            'reason': VISUAL_REVIEW_REASON,
                        })
                    else:
                        matches.append({'set_id': item.id, 'discord_root_id': post.id,
                                        'evidence': evidence,
                                        'stored_signature': media_signature(stored_item),
                                        'discord_signature': media_signature(post)})
    return matches, unresolved


def cleanup(*, apply: bool, confirmed: bool = True, verify_media: bool = True,
            limit: int = 10000, allow_visual: bool | None = None) -> dict:
    """Report or archive and remove duplicate sets. Discord rows are immutable."""
    with POOL.connection() as connection, connection.cursor() as cursor:
        discord, goyangi = db.load_sets(cursor)
        blocked = db.blocked_sets(cursor)
    # Fingerprinting can use another pooled connection, so never retain the
    # snapshot connection while making network requests or decoding videos.
    matches, unresolved = _plan(
        discord, goyangi, blocked, confirmed=confirmed, verify_media=verify_media,
        limit=limit, cache_writes=apply,
        allow_visual=visual_auto_dedupe_enabled() if allow_visual is None else allow_visual,
    )
    deleted = 0
    if apply and matches:
        with POOL.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(db.LOCK_SQL)
            # Re-read sets under the serialization lock before deleting.
            current_discord, current_goyangi = db.load_sets(cursor)
            current_posts = {p.id: p for p in current_discord}
            current_sets = {s.id: s for s in current_goyangi}
            for match in matches:
                sid, root = match['set_id'], match['discord_root_id']
                current = current_sets.get(sid)
                post = current_posts.get(root)
                signatures_unchanged = (
                    current is not None and post is not None
                    and media_signature(current) == match['stored_signature']
                    and media_signature(post) == match['discord_signature']
                )
                if signatures_unchanged:
                    deleted += db.remove_set(cursor, sid, root, match['evidence'])
    return {'matches': matches, 'unresolved': unresolved, 'deleted': deleted,
            'dry_run': not apply}
