"""One-time scrape: build the sorter catalog as a single static JSON file.

Reads the kpopidolsorter dataset (characterData + group options) and the
live Postgres ``role_info`` table, then writes
``static/sorter/catalog.json`` — the only artifact the web sorter loads.

For every sorter entry we attach, when a strict normalized
``(member, group)`` match exists in ``role_info``:

- ``role_id`` — so sorter votes can update ELO
- ``photo`` — the kpopping/legacy portrait from ``role_info.image_url``,
  overwriting the old Imgur asset

Unmatched entries keep ``photo: null`` / ``role_id: null`` and the frontend
falls back to the original Imgur URL. No runtime SQL + file lookup mix, no
vendored image binaries in the Heroku slug.

Usage:
    python scripts/build_sorter_catalog.py \
        --dataset /Users/wesleyliao/kpopidolsorter/src/js/data/2025-11-01.js \
        --output static/sorter/catalog.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env.local", override=True)

IMAGE_ROOT = "https://i.imgur.com/"
# Vendored copy of kpopidolsorter/src/assets/idols, served same-origin so no
# hotlinking is involved. kpopping/legacy hosts sit behind Cloudflare bot
# checks and cannot be hotlinked, so local photos are the primary asset.
IDOL_DIR_URL = "/static/sorter/idols"

# Sorter-side normalized group name -> DB-side normalized group name.
# Covers renames where strict matching would miss (e.g. G-IDLE -> i-dle).
GROUP_ALIASES = {
    "idle": "gidle",  # sorter "i-dle", db "G-IDLE"
    "ohmygirl": "omg",  # sorter "Oh My Girl", db "OMG"
}


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


GROUP_ALIASES[_norm("Girls' Generation")] = _norm("SNSD")

# Hand-picked group covers (vendored under static/sorter/idols/). These win
# over the photo-cache mapping for the matching group-card entry.
GROUP_COVER_OVERRIDES = {
    "BABYMONSTER": "/static/sorter/idols/2ea62874969fbb70.jpg",  # Vogue SG banner, Sep 2024
}

IDOL_PHOTO_OVERRIDES = {
    # Hand-picked sorter portraits (vendored under static/sorter/idols/).
    # These win over the photo-cache mapping and survive rebuilds.
    "tripleS Kim Yooyeon": "/static/sorter/idols/tripleS-kim-yooyeon.jpg",
    "tripleS Kim Chaeyeon": "/static/sorter/idols/tripleS-kim-chaeyeon.jpg",
    # https://pbs.twimg.com/media/HJ-PgnfbUAANeNF?format=jpg&name=large
    "Hyewon": "/static/sorter/idols/kang-hyewon-HJ-PgnfbUAANeNF.jpg",
    # https://wimg.heraldcorp.com/news/cms/2026/03/31/news-p.v1.20260331.93dd06d29c144fb2988bd39cd3c1923c_P1.jpg
    "Kwon Eunbi": "/static/sorter/idols/kwon-eunbi-herald-20260331.jpg",
}


# role_id -> vendored portrait, merged into role-photos.json at build time.
# For catalog entries without role_id (name mismatch with role_info), so the
# builder can't link them itself. They take effect on the board because
# BACKFILL_EXCLUDE keeps the same roles out of embed-photos.json.
ROLE_PHOTO_OVERRIDES = {
    "1079677939878219826": "/static/sorter/idols/tripleS-kim-yooyeon.jpg",  # Yooyeon, tripleS
    "1000867801420009502": "/static/sorter/idols/tripleS-kim-chaeyeon.jpg",  # Chaeyeon, tripleS
}


# Members missing from the upstream dataset, kept here so rebuilds don't drop
# them again. They are appended AFTER group cards so existing numeric ids
# (saved sessions, shared result links) never shift.
MANUAL_IDOLS = [
    {
        "name": "LOOSSEMBLE Hyunjin",
        "img": "",
        "groups": ["LOOSSEMBLE"],
        "gen": ["gen4"],
    },
]


def _parse_dataset(path: Path) -> tuple[str, list[dict], list[dict], list[dict]]:
    """Return (version, group_defs, idol_entries, group_cards)."""
    src = path.read_text()
    version_match = re.search(r'dataSetVersion\s*=\s*"([^"]+)"', src)
    version = version_match.group(1) if version_match else "unknown"

    group_defs: list[dict] = []
    group_block = re.search(r'key:\s*"group".*?sub:\s*\[(.*?)\]\s*\n\s*\}', src, re.S)
    if group_block:
        for m in re.finditer(
            r'\{\s*name:\s*"([^"]+)",\s*key:\s*"([^"]+)"'
            r'(?:,\s*gen:\s*\[(.*?)\])?',
            group_block.group(1),
        ):
            name, key, gen_raw = m.group(1), m.group(2), m.group(3) or ""
            group_defs.append(
                {"name": name, "key": key, "gen": re.findall(r'"([^"]+)"', gen_raw)}
            )

    idol_entries: list[dict] = []
    group_cards: list[dict] = []
    entry_re = re.compile(
        r'name:\s*"([^"]+)",\s*\n\s*img:\s*"([^"]+)",\s*\n\s*opts:\s*\{\s*\n(.*?)'
        r'\n\s*\}\s*\n\s*\}',
        re.S,
    )
    for m in entry_re.finditer(src):
        name, img, opts = m.group(1), m.group(2), m.group(3)
        group_match = re.search(r'group:\s*\[(.*?)\]', opts)
        label_match = re.search(r'group_label:\s*\[(.*?)\]', opts)
        gen_match = re.search(r'gen:\s*\[(.*?)\]', opts)
        gen = re.findall(r'"([^"]+)"', gen_match.group(1)) if gen_match else []
        if group_match:
            idol_entries.append(
                {
                    "name": name,
                    "img": img,
                    "groups": re.findall(r'"([^"]+)"', group_match.group(1)),
                    "gen": gen,
                }
            )
        elif label_match:
            group_cards.append(
                {
                    "name": name,
                    "img": img,
                    "group_labels": re.findall(r'"([^"]+)"', label_match.group(1)),
                }
            )
    # Keep the sorter classification consistent across groups and members.
    for group in group_defs:
        if group["key"] == "tripleS":
            group["gen"] = ["gen5"]
    for idol in idol_entries:
        if "tripleS" in idol["groups"]:
            idol["gen"] = ["gen5"]
    # FIFTY FIFTY's "second generation": Keena debuted November 2022
    # (gen4); Chanelle, Yewon, Hana, and Athena debuted September 2024
    # (gen5). The group as it exists today promotes in the gen-5 era.
    for group in group_defs:
        if group["key"] == "FIFTY FIFTY":
            group["gen"] = ["gen5"]
    for idol in idol_entries:
        if "FIFTY FIFTY" in idol["groups"] and idol["name"] != "FIFTY FIFTY Keena":
            idol["gen"] = ["gen5"]
    return version, group_defs, idol_entries, group_cards


def _load_photo_cache(path: Path | None) -> dict[str, str]:
    """Map sorter img refs to vendored same-origin URLs.

    photo-cache.js maps e.g. ``"0LwnBfv.jpeg": "src/assets/idols/057a....jpg"``.
    Returns ``{img_ref: "/static/sorter/idols/057a....jpg"}`` for files that
    actually exist under ``static/sorter/idols/``.
    """
    if path is None:
        path = REPO_ROOT / "static" / "sorter" / "photo-cache.js"
        candidates = [path]
    else:
        candidates = [path]
    src = None
    for candidate in candidates:
        if candidate.exists():
            src = candidate.read_text()
            break
    if src is None:
        # Fall back to the upstream sorter repo location.
        upstream = Path("/Users/wesleyliao/kpopidolsorter/src/js/photo-cache.js")
        src = upstream.read_text() if upstream.exists() else ""
    idol_dir = REPO_ROOT / "static" / "sorter" / "idols"
    mapping: dict[str, str] = {}
    for img_ref, local_file in re.findall(r'"([^"]+)":\s*"src/assets/idols/([^"]+)"', src):
        if (idol_dir / local_file).exists():
            mapping[img_ref] = f"{IDOL_DIR_URL}/{local_file}"
    print(f"photo cache: {len(mapping)} vendored images available")
    return mapping


def _local_photo(local_photos: dict[str, str], img: str) -> str | None:
    # Cache keys are usually imgur hashes, but a few group cards use absolute
    # URLs (billboard/wikimedia/fandom) that were vendored under their full URL.
    return local_photos.get(img)


def _short_name(name: str, groups: list[str]) -> str:
    for group in groups:
        prefix = group.lower() + " "
        if name.lower().startswith(prefix):
            return name[len(group) + 1 :]
    return name


def _load_roles() -> list[tuple[str, str, str, str | None, int]]:
    import psycopg

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    with psycopg.connect(database_url, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role_id, member_name, group_name, image_url,
                       COALESCE(global_elo, 1200)
                FROM role_info;
                """
            )
            return [
                (role_id, member or "", group or "", image or None, elo)
                for role_id, member, group, image, elo in cur.fetchall()
            ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Path to sorter data JS file")
    parser.add_argument("--output", required=True, help="Output catalog.json path")
    parser.add_argument(
        "--photo-cache",
        default=None,
        help="Path to sorter photo-cache.js (img ref -> vendored local file)",
    )
    parser.add_argument(
        "--role-photos",
        default=None,
        help="Output role-photos.json path ({role_id: local photo URL} for the leaderboard)",
    )
    args = parser.parse_args()

    version, group_defs, idol_entries, group_cards = _parse_dataset(Path(args.dataset))
    print(f"dataset {version}: {len(group_defs)} groups, {len(idol_entries)} idols, {len(group_cards)} group cards")

    local_photos = _load_photo_cache(Path(args.photo_cache) if args.photo_cache else None)

    roles = _load_roles()
    by_member_group = {
        (_norm(member), GROUP_ALIASES.get(_norm(group), _norm(group))): (role_id, image)
        for role_id, member, group, image, _ in roles
        if member.strip()
    }
    # Best portrait per db group (highest ELO with an image) for group cards.
    best_group_photo: dict[str, tuple[str, str]] = {}
    for role_id, member, group, image, elo in roles:
        if not image:
            continue
        key = GROUP_ALIASES.get(_norm(group), _norm(group))
        if key not in best_group_photo or elo > best_group_photo[key][0]:
            best_group_photo[key] = (elo, image)

    def resolve_group_key(key: str) -> str:
        return GROUP_ALIASES.get(_norm(key), _norm(key))

    def build_idol_entry(index: int, item: dict) -> tuple[dict, bool, bool]:
        short = _short_name(item["name"], item["groups"])
        role_id: str | None = None
        photo: str | None = None
        for group in item["groups"]:
            hit = by_member_group.get((_norm(short), resolve_group_key(group)))
            if hit:
                role_id, photo = hit
                break
        img = item["img"]
        return (
            {
                "id": index,
                "kind": "idol",
                "name": item["name"],
                "short": short,
                "group": item["groups"][0] if item["groups"] else "",
                "groups": item["groups"],
                "gen": item["gen"],
                "img": img,
                "fallback": img
                if img.startswith("http")
                else (IMAGE_ROOT + img if img else None),
                "local": IDOL_PHOTO_OVERRIDES.get(item["name"]) or (_local_photo(local_photos, img) if img else None),
                "photo": photo,
                "role_id": role_id,
            },
            role_id is not None,
            photo is not None,
        )

    entries: list[dict] = []
    matched_idols = 0
    matched_photos = 0
    for index, item in enumerate(idol_entries):
        entry, has_role, has_photo = build_idol_entry(index, item)
        if has_role:
            matched_idols += 1
        if has_photo:
            matched_photos += 1
        entries.append(entry)

    base = len(entries)
    matched_groups = 0
    for offset, card in enumerate(group_cards):
        photo = None
        for label in card["group_labels"]:
            hit = best_group_photo.get(resolve_group_key(label))
            if hit:
                photo = hit[1]
                matched_groups += 1
                break
        local = _local_photo(local_photos, card["img"])
        override = GROUP_COVER_OVERRIDES.get(card["name"])
        if override:
            local = override
        entries.append(
            {
                "id": base + offset,
                "kind": "group",
                "name": card["name"],
                "short": card["name"],
                "group": card["group_labels"][0] if card["group_labels"] else card["name"],
                "groups": [],
                "gen": [],
                "img": card["img"],
                "fallback": card["img"]
                if card["img"].startswith("http")
                else IMAGE_ROOT + card["img"],
                "local": local,
                "photo": photo,
                "role_id": None,
            }
        )

    manual_count = 0
    for item in MANUAL_IDOLS:
        entry, has_role, has_photo = build_idol_entry(len(entries), item)
        if has_role:
            matched_idols += 1
        if has_photo:
            matched_photos += 1
        entries.append(entry)
        manual_count += 1
    print(f"manual idols appended: {manual_count}")

    role_photos = {
        entry["role_id"]: entry["local"]
        for entry in entries
        if entry["role_id"] and entry["local"]
    }
    # Hand-picked board portraits for idols whose catalog entries carry no
    # role_id (name mismatch with role_info), so the builder can't link them.
    # These win over generated mappings and over Discord embeds (the board
    # prefers ROLE_PHOTOS to EMBED_PHOTOS). Keep BACKFILL_EXCLUDE in
    # backfill_embed_photos.py in sync so reruns don't resurrect embeds.
    role_photos.update(ROLE_PHOTO_OVERRIDES)
    role_photos_path = (
        Path(args.role_photos)
        if args.role_photos
        else Path(args.output).parent / "role-photos.json"
    )
    role_photos_path.write_text(json.dumps(role_photos))
    local_count = sum(1 for entry in entries if entry["local"])
    print(f"wrote {role_photos_path} ({len(role_photos)} votable idols with local photos)")
    print(f"entries with vendored photos: {local_count}/{len(entries)}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "image_note": "photo is the kpopping overwrite from role_info.image_url; fallback is the original sorter Imgur asset",
        "stats": {
            "idols": len(idol_entries),
            "manual_idols": manual_count,
            "group_cards": len(group_cards),
            "matched_idols": matched_idols,
            "matched_photos": matched_photos,
            "matched_group_cards": matched_groups,
        },
        "groups": group_defs,
        "entries": entries,
    }
    output_path.write_text(json.dumps(payload))
    size_kb = output_path.stat().st_size / 1024
    print(f"wrote {output_path} ({size_kb:.0f} KB)")
    print(
        f"matched idols: {matched_idols}/{len(idol_entries)}, "
        f"with photo: {matched_photos}, group cards with photo: {matched_groups}/{len(group_cards)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
