#!/usr/bin/env python3
"""Rewrite stored Imgur album page URLs to their playable file (one-off).

Scope: only albums on Discord posts that are candidates for some Goyangi set
(same role within 24h) can ever match, so everything else is skipped by
construction. No Discord API, no Imgur API key, no media downloads: one
keyless album-page GET per album, polite single-threaded pace.

Each stored /a/ URL demonstrably unfurled to exactly one playable file when
ingested (ingestion only stores playable embeds), so resolving to that file
loses nothing. Collisions (same post+role already has the mp4 row) are
skipped and reported, never merged.

Usage (dry run first):
  heroku run --no-tty --exit-code -a hanni-dev -- python scripts/backfill_album_urls.py
  heroku run --no-tty --exit-code -a hanni-dev -- python scripts/backfill_album_urls.py --apply
"""
from __future__ import annotations

import random
import re
import sys
import time

import requests

IMGUR_PAGE = "https://imgur.com/a/{album}"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

PAGE_DELAY_SECONDS = 1.2


def _cover_mp4(html: str) -> str | None:
    """Return the playable mp4 behind an album page, preferring video files."""
    found = re.findall(r"i\.imgur\.com/([A-Za-z0-9]+)\.(mp4|gif|jpg|jpeg|png|webp)", html)
    for ident, ext in found:
        if ext in ("mp4", "gif"):
            return f"https://i.imgur.com/{ident}.mp4"
    return None


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    from psycopg import errors

    from src.content_dedup import candidate_posts, MediaSetIndex, media_key
    from src.db import POOL

    POOL.open()
    try:
        with POOL.connection() as conn, conn.cursor() as cur:
            from src.db import content_dedup as db

            discord, goyangi = db.load_sets(cur)
        index = MediaSetIndex(discord)
        albums: set[str] = set()
        for item in goyangi:
            for post in candidate_posts(item, index):
                for url in post.urls:
                    key = media_key(url)
                    if key.startswith("imgur:album:"):
                        albums.add(key.rsplit(":", 1)[-1])
        print(f"in-scope albums: {len(albums)}", flush=True)

        session = requests.Session()
        session.headers.update({"User-Agent": BROWSER_UA})
        rewritten = skipped = failed = 0
        failures: list[str] = []
        for i, album in enumerate(sorted(albums)):
            with POOL.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM content_links "
                    "WHERE source_message_id IS NOT NULL AND url LIKE %s",
                    ("%/a/%" + album + "%",),
                )
                pending = cur.fetchone()[0]
            if not pending:
                continue
            try:
                response = session.get(IMGUR_PAGE.format(album=album), timeout=(5, 15))
                if response.status_code != 200:
                    failed += 1
                    failures.append(f"{album}: page HTTP {response.status_code}")
                    continue
                mp4 = _cover_mp4(response.text)
            except requests.RequestException as error:
                failed += 1
                failures.append(f"{album}: {type(error).__name__}")
                continue
            finally:
                time.sleep(PAGE_DELAY_SECONDS + random.uniform(0, 0.6))
            if mp4 is None:
                failed += 1
                failures.append(f"{album}: no playable file on page")
                continue
            if not apply:
                rewritten += pending
                continue
            with POOL.connection() as conn, conn.transaction(), conn.cursor() as cur:
                try:
                    cur.execute(
                        "UPDATE content_links SET url = %s "
                        "WHERE source_message_id IS NOT NULL AND url LIKE %s",
                        (mp4, "%/a/%" + album + "%"),
                    )
                    rewritten += cur.rowcount
                except errors.UniqueViolation:
                    conn.rollback()
                    skipped += pending
                    failures.append(f"{album}: target mp4 row already exists, skipped")
            if (i + 1) % 50 == 0:
                print(f"progress {i + 1}/{len(albums)} rewritten={rewritten} "
                      f"skipped={skipped} failed={failed}", flush=True)
        print(f"DONE apply={apply} rewritten={rewritten} skipped={skipped} "
              f"failed={failed}", flush=True)
        for failure in failures[:20]:
            print("  " + failure, flush=True)
        return 0
    finally:
        POOL.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
