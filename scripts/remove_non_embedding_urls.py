#!/usr/bin/env python3
"""Delete content_links rows Discord cannot unfurl as inline gif-like embeds.

File lockers, wrapper pages, and raw video CDNs (see NON_EMBEDDING_MEDIA_HOSTS
in src/config/constants.py) ingest as bare links or unplayable videos, so
remove them from existing links. Ingestion already skips these hosts.

Safety:
- Matches on the URL hostname only (exact or subdomain), case-insensitive.
- No FKs reference content_links, so a plain DELETE is safe.
- Default is a dry run that prints every doomed row. Pass --apply to delete.

Usage:
  python scripts/remove_non_embedding_urls.py
  python scripts/remove_non_embedding_urls.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.config.constants import NON_EMBEDDING_MEDIA_HOSTS  # noqa: E402
from src.db import POOL  # noqa: E402


def _is_non_embedding_url(url: object) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".") if isinstance(url, str) else ""
    return any(host == blocked or host.endswith("." + blocked) for blocked in NON_EMBEDDING_MEDIA_HOSTS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove non-embedding URLs from content_links")
    parser.add_argument("--apply", action="store_true", help="delete rows (default: dry run)")
    args = parser.parse_args(argv)

    POOL.open()
    try:
        with POOL.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT content_link_id, role_id, url, source_kind FROM content_links")
            rows = cur.fetchall()
            doomed = [row for row in rows if _is_non_embedding_url(row[2])]
            for content_link_id, role_id, url, source_kind in sorted(doomed):
                print(f"  {content_link_id} [{source_kind}/{role_id}] {url}")
            print(f"{len(doomed)} of {len(rows)} rows match {sorted(NON_EMBEDDING_MEDIA_HOSTS)}")
            if not args.apply:
                print("dry run: no deletes (pass --apply to delete)")
                return 0
            if doomed:
                cur.execute(
                    "DELETE FROM content_links WHERE content_link_id = ANY(%s)",
                    ([row[0] for row in doomed],),
                )
                print(f"deleted: {cur.rowcount}")
            conn.commit()
    finally:
        POOL.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
