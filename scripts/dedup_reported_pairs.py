#!/usr/bin/env python3
"""One-off visual verification for user-reported Discord <-> Goyangi pairs.

Why this exists: Heroku egress is rate-limited by Imgur (API + media both
429), so the server-side backfill can never expand albums to compare them.
Your laptop network is not limited, so run the comparison from here.

Resolves each reported URL pair to its stored Goyangi set + Discord post,
runs Fingerprinter.match_contents (same thresholds as production), and prints
per-pair results. With --apply, archives/removes confirmed duplicate Goyangi
clips. Discord rows are never touched (remove_content enforces this).

Usage (read-only dry run first):
  DATABASE_URL="$(heroku config:get DATABASE_URL -a hanni-dev)" \
  IMGUR_CLIENT_ID="$(heroku config:get IMGUR_CLIENT_ID -a hanni-dev)" \
  PYTHONPATH="$PWD" python3 scripts/dedup_reported_pairs.py

Then to actually remove confirmed dupes:
  ... python3 scripts/dedup_reported_pairs.py --apply

Takes ~20-40 min for all pairs. Safe to leave running; progress prints per pair.
"""
from __future__ import annotations

import re
import sys

PAIRS: list[tuple[str, str]] = [
    # Original examples (expected: mostly already cleaned as whole sets).
    ("https://imgur.com/LTE2dNM",
     "https://cdn.goyangi.pics/v1/itzy/yuna/260927-itzy-yuna-12c3.webp"),
    ("https://imgur.com/a/asa-x-babymonster-capo-IIKllKi",
     "https://cdn.goyangi.pics/v1/babymonster/asa/260927-babymonster-asa-1dcc.webp"),
    ("https://imgur.com/a/jang-wonyoung-x-ive-capo-ebTlCEV",
     "https://cdn.goyangi.pics/v1/ive/wonyoung/260925-ive-wonyoung-6c55.webp"),
    ("https://imgur.com/QWIx6UJ",
     "https://cdn.goyangi.pics/v1/aespa/260920-aespa-karina-winter-2f14.webp"),
    ("https://imgur.com/a/lisa-x-blackpink-capo-KpgAn44",
     "https://cdn.goyangi.pics/v1/blackpink/lisa/260925-blackpink-lisa-1764.webp"),
    ("https://imgur.com/a/ningning-x-aespa-capo-Yk9o65b",
     "https://cdn.goyangi.pics/v1/aespa/ningning/260925-aespa-ningning-72a2.webp"),
    # Second batch.
    ("https://imgur.com/a/yeji-x-itzy-capo-Y0HGjv5",
     "https://cdn.goyangi.pics/v1/itzy/yeji/260927-itzy-yeji-ec16.webp"),
    ("https://imgur.com/a/chaeryeong-x-itzy-capo-EdW1ZG8",
     "https://cdn.goyangi.pics/v1/itzy/chaeryeong/260927-itzy-chaeryeong-0891.webp"),
    ("https://imgur.com/a/asa-x-babymonster-capo-x9kkj69",
     "https://cdn.goyangi.pics/v1/babymonster/asa/260911-babymonster-asa-f6bf.webp"),
    ("https://imgur.com/a/lisa-x-blackpink-capo-GUrSnp4",
     "https://cdn.goyangi.pics/v1/blackpink/lisa/260926-blackpink-lisa-321b.webp"),
    ("https://imgur.com/a/mina-x-twice-capo-dBhMIjA",
     "https://cdn.goyangi.pics/v1/twice/mina/260923-twice-mina-a90c.webp"),
    # Third batch (visually confirmed by reporter).
    ("https://imgur.com/a/chaeryeong-x-itzy-capo-YT2pBBu",
     "https://cdn.goyangi.pics/v1/itzy/chaeryeong/260927-itzy-chaeryeong-c473.webp"),
    ("https://imgur.com/a/asa-x-babymonster-capo-6RPwmWo",
     "https://cdn.goyangi.pics/v1/babymonster/asa/260911-babymonster-asa-2d09.webp"),
    ("https://imgur.com/a/winter-x-aespa-capo-KMCtVlK",
     "https://cdn.goyangi.pics/v1/aespa/winter/260823-aespa-winter-7a3c.webp"),
]


def _imgur_id(url: str) -> str | None:
    tail = url.rstrip("/").rsplit("/", 1)[-1].split(".")[0]
    ident = tail.rsplit("-", 1)[-1]
    return ident if re.fullmatch(r"[A-Za-z0-9]{5,10}", ident) else None


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    from src.db import POOL
    from src.db import content_dedup as db
    from src.services.content_fingerprint import Fingerprinter

    POOL.open()
    try:
        with POOL.connection() as conn, conn.cursor() as cur:
            discord_all, goyangi_all = db.load_sets(cur)
        discord_by_id = {p.id: p for p in discord_all}
        goyangi_by_id = {g.id: g for g in goyangi_all}
        # URL -> owning set/root for quick resolution.
        url_owner: dict[str, tuple[str, str]] = {}
        with POOL.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT url, source_kind, goyangi_set_id,
                          COALESCE(root_message_id, source_message_id)
                   FROM content_links
                   WHERE source_kind = 'goyangi' OR source_message_id IS NOT NULL"""
            )
            for url, kind, sid, root in cur.fetchall():
                if isinstance(url, bytes):
                    url = url.decode()
                url_owner.setdefault(url, (kind, sid or root))

        total_deleted = 0
        for i, (d_url, g_url) in enumerate(PAIRS):
            print(f"[{i + 1}/{len(PAIRS)}] {d_url} <-> {g_url}", flush=True)
            # Resolve Goyangi side.
            grows = url_owner.get(g_url)
            if grows is None and (iid := _imgur_id(g_url)) is None:
                tail = g_url.rsplit("/", 1)[-1].split(".")[0][-8:]
                grows = next(
                    ((k, v) for u, (k, v) in url_owner.items() if tail in u), None
                )
            if grows is None or grows[0] != "goyangi":
                # Maybe already removed as a confirmed dupe?
                with POOL.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "SELECT content_id, discord_root_id FROM "
                        "goyangi_duplicate_contents WHERE %s LIKE '%%' || "
                        "content_id || '%%'",
                        (g_url,),
                    )
                    prior = cur.fetchall()
                print(f"  goyangi row gone; prior suppressions: {prior}", flush=True)
                continue
            g_set = goyangi_by_id.get(grows[1])
            if g_set is None:
                print("  goyangi set fully removed already (clean)", flush=True)
                continue
            # Resolve Discord side.
            d_candidates = []
            if (hit := url_owner.get(d_url)) and hit[0] != "goyangi":
                d_candidates = [hit[1]]
            elif (iid := _imgur_id(d_url)):
                with POOL.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT COALESCE(root_message_id, "
                        "source_message_id) FROM content_links "
                        "WHERE source_message_id IS NOT NULL AND url LIKE %s",
                        ("%" + iid + "%",),
                    )
                    d_candidates = [r[0] for r in cur.fetchall()]
            posts = [discord_by_id[r] for r in d_candidates if r in discord_by_id]
            if not posts:
                print("  discord side not in DB; skipping", flush=True)
                continue
            fp = Fingerprinter(cache_writes=True, seconds=180)
            try:
                found, complete = fp.match_contents(g_set, posts, visual=True)
            except Exception as error:  # noqa: BLE001 - report and continue
                print(f"  verification error: {type(error).__name__}: "
                      f"{str(error)[:120]}", flush=True)
                continue
            print(f"  set={g_set.id} posts={[p.id for p in posts]} "
                  f"matches={[(m['content_id'], m['evidence'].get('kind')) for m in found]} "
                  f"complete={complete}", flush=True)
            if apply and found:
                with POOL.connection() as conn, conn.transaction(), \
                        conn.cursor() as cur:
                    cur.execute(db.LOCK_SQL)
                    for m in found:
                        if not m.get("content_id"):
                            continue
                        try:
                            total_deleted += db.remove_content(
                                cur, m["content_id"], m["role_id"], g_set.id,
                                m["discord_root_id"], m["evidence"],
                            )
                        except ValueError as error:
                            print(f"  skip {m['content_id']}: {error}", flush=True)
                print(f"  deleted so far: {total_deleted}", flush=True)
        print(f"DONE deleted_contents={total_deleted} apply={apply}", flush=True)
        return 0
    finally:
        POOL.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
