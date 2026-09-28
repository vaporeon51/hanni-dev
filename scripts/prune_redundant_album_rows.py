#!/usr/bin/env python3
"""Delete redundant /a/ rows whose playable file already has a row.

After backfill_album_urls.py, a few (message, role) pairs hold both the album
page row and its playable mp4 row (same GIF posted both ways, or embedded
twice). The /a/ row adds a duplicate feed item, so remove it.

Safety:
- Only touches /a/ rows in dedupe scope (candidate posts of Goyangi sets).
- Only deletes when the resolved mp4 row exists for the SAME message+role.
- Transfers user counters (upvotes/reports/dead-link reports) to the
  survivor; reaction counts stay with the survivor (same message).
- No FKs reference content_links; recovery-item references abort that row.
- Default is a dry run that prints every doomed row. Pass --apply to delete.

Usage:
  heroku run --no-tty --exit-code -a hanni-dev -- python scripts/prune_redundant_album_rows.py
  heroku run --no-tty --exit-code -a hanni-dev -- python scripts/prune_redundant_album_rows.py --apply
"""
from __future__ import annotations

import re
import sys
import time

import requests

BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _cover_mp4(html: str) -> str | None:
    found = re.findall(r"i\.imgur\.com/([A-Za-z0-9]+)\.(mp4|gif|jpg|jpeg|png|webp)", html)
    for ident, ext in found:
        if ext in ("mp4", "gif"):
            return f"https://i.imgur.com/{ident}.mp4"
    return None


def main(argv: list[str]) -> int:
    apply = "--apply" in argv

    from src.content_dedup import candidate_posts, MediaSetIndex, media_key
    from src.db import POOL
    from src.db import content_dedup as db

    POOL.open()
    try:
        with POOL.connection() as conn, conn.cursor() as cur:
            discord, goyangi = db.load_sets(cur)
        index = MediaSetIndex(discord)
        albums: set[str] = set()
        for item in goyangi:
            for post in candidate_posts(item, index):
                for url in post.urls:
                    key = media_key(url)
                    if key.startswith("imgur:album:"):
                        albums.add(key.rsplit(":", 1)[-1])

        session = requests.Session()
        session.headers.update({"User-Agent": BROWSER_UA})
        doomed: list[tuple[int, int, str, str]] = []  # (dupe_id, keep_id, album, mp4)
        blocked: list[str] = []
        for album in sorted(albums):
            with POOL.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT content_link_id, source_message_id, role_id FROM content_links "
                    "WHERE source_message_id IS NOT NULL AND url LIKE %s",
                    ("%/a/%" + album + "%",),
                )
                album_rows = cur.fetchall()
            if not album_rows:
                continue
            try:
                response = session.get(f"https://imgur.com/a/{album}", timeout=(5, 15))
                mp4 = _cover_mp4(response.text) if response.status_code == 200 else None
            except requests.RequestException:
                mp4 = None
            time.sleep(1.2)
            if mp4 is None:
                continue
            with POOL.connection() as conn, conn.cursor() as cur:
                for dupe_id, message_id, role_id in album_rows:
                    cur.execute(
                        "SELECT content_link_id FROM content_links "
                        "WHERE source_message_id = %s AND role_id = %s AND url = %s",
                        (message_id, role_id, mp4),
                    )
                    keep = cur.fetchone()
                    if keep and keep[0] != dupe_id:
                        cur.execute(
                            "SELECT count(*) FROM content_link_recovery_items "
                            "WHERE content_link_id = %s",
                            (dupe_id,),
                        )
                        if cur.fetchone()[0]:
                            blocked.append(f"{album}: row {dupe_id} has recovery items")
                        else:
                            doomed.append((dupe_id, keep[0], album, mp4))
        print(f"redundant /a/ rows: {len(doomed)} blocked: {len(blocked)}", flush=True)
        for dupe_id, keep_id, album, mp4 in doomed:
            print(f"  del {dupe_id} ({album}) keep {keep_id} ({mp4})", flush=True)
        for line in blocked:
            print("  BLOCKED " + line, flush=True)
        if apply and doomed:
            with POOL.connection() as conn, conn.transaction(), conn.cursor() as cur:
                cur.execute(db.LOCK_SQL)
                for dupe_id, keep_id, _album, _mp4 in doomed:
                    cur.execute(
                        "UPDATE content_links survivor SET "
                        "num_upvotes = survivor.num_upvotes + dupe.num_upvotes, "
                        "num_reports = survivor.num_reports + dupe.num_reports, "
                        "dead_link_reports = survivor.dead_link_reports + dupe.dead_link_reports "
                        "FROM content_links dupe "
                        "WHERE survivor.content_link_id = %s AND dupe.content_link_id = %s",
                        (keep_id, dupe_id),
                    )
                    cur.execute(
                        "DELETE FROM content_links WHERE content_link_id = %s", (dupe_id,)
                    )
            print(f"deleted {len(doomed)}", flush=True)
        elif not apply:
            print("dry run; pass --apply to delete", flush=True)
        return 0
    finally:
        POOL.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
