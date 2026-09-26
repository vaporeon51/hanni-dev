"""One-time backfill: harvest Discord-proxied kpopping portraits.

kpopping/legacy image hosts 403 every direct hotlink (Cloudflare), but
Discord's unfurl crawler fetches them fine and re-serves the same bytes via
its hotlinkable ``images-ext`` proxy (no auth, no expiry, deterministic per
source URL). This script posts each ``role_info.image_url`` through the
private dead-link webhook, harvests the proxy URL, and writes
``static/sorter/embed-photos.json``::

    {"by_role": {role_id: proxy_url}, "by_source": {source_url: proxy_url}}

Boards prefer these over the vendored sorter portraits. Probe messages are
deleted as we go. Reruns skip role_ids already harvested.

Usage:
    python scripts/backfill_embed_photos.py [--limit 5] [--gap 2.0]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Hand-picked portraits (ROLE_PHOTO_OVERRIDES in build_sorter_catalog.py).
# Never harvest these: with no embed entry the board serves the vendored
# file, and reruns must not resurrect the harvested URLs.
BACKFILL_EXCLUDE = frozenset({
    "1079677939878219826",  # Yooyeon, tripleS
    "1000867801420009502",  # Chaeyeon, tripleS
    "1141805269978980452",  # Seoyeon, tripleS
    "1313202769938878514",  # Hayeon, tripleS
})

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env.local", override=True)

from src.services.discord_embed_probe import harvest_embed_image  # noqa: E402


def _load_roles() -> list[tuple[str, str]]:
    import psycopg

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    with psycopg.connect(database_url, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role_id, image_url FROM role_info
                WHERE image_url IS NOT NULL
                  AND LOWER(image_url) LIKE '%kpopping%'
                ORDER BY member_name;
                """
            )
            return [(role_id, image) for role_id, image in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Harvest at most N (0 = all)")
    parser.add_argument("--gap", type=float, default=2.0, help="Seconds between probes")
    args = parser.parse_args()

    webhook_url = os.getenv("DISCORD_DEAD_LINK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError("DISCORD_DEAD_LINK_WEBHOOK_URL is required")

    output_path = REPO_ROOT / "static" / "sorter" / "embed-photos.json"
    existing: dict = {"by_role": {}, "by_source": {}}
    if output_path.exists():
        existing = json.loads(output_path.read_text())
    by_role: dict[str, str] = {
        role_id: url for role_id, url in existing.get("by_role", {}).items()
        if role_id not in BACKFILL_EXCLUDE
    }
    by_source: dict[str, str] = dict(existing.get("by_source", {}))

    roles = _load_roles()
    pending = [
        (role_id, url) for role_id, url in roles
        if role_id not in by_role and role_id not in BACKFILL_EXCLUDE
    ]
    if args.limit > 0:
        pending = pending[: args.limit]
    print(f"{len(roles)} kpopping portraits, {len(by_role)} already harvested, {len(pending)} pending")

    failures: list[tuple[str, str]] = []
    try:
        for index, (role_id, url) in enumerate(pending):
            harvest = harvest_embed_image(url, webhook_url=webhook_url)
            if harvest.proxy_url:
                by_role[role_id] = harvest.proxy_url
                by_source[url] = harvest.proxy_url
                print(f"[{index + 1}/{len(pending)}] ok {role_id} {harvest.width}x{harvest.height}")
            else:
                failures.append((role_id, harvest.error or "unknown"))
                print(f"[{index + 1}/{len(pending)}] FAIL {role_id}: {harvest.error}")
            if (index + 1) % 10 == 0:
                output_path.write_text(json.dumps({"by_role": by_role, "by_source": by_source}))
            time.sleep(max(0.0, args.gap))
    finally:
        output_path.write_text(json.dumps({"by_role": by_role, "by_source": by_source}))

    print(f"done: {len(by_role)} harvested, {len(failures)} failures")
    for role_id, error in failures:
        print(f"  FAIL {role_id}: {error}")
    return 0 if not pending or len(failures) < len(pending) else 1


if __name__ == "__main__":
    raise SystemExit(main())
