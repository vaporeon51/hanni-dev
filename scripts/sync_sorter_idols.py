"""Sync catalog identities after migration 39, without resetting live Elo.

Default: offline dry run. --apply writes metadata and verifies coverage.
--check verifies coverage without writes. Both use the intended DATABASE_URL.
"""
from pathlib import Path
import argparse
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import psycopg
from src.sorter_catalog import catalog_rating_rows


def check_catalog_ratings(conn, rows):
    expected = {row[0] for row in rows}
    found = {row[0] for row in conn.execute(
        'SELECT role_id FROM idol_ratings WHERE role_id = ANY(%s)', (list(expected),))}
    missing = expected - found
    if missing:
        raise RuntimeError(f'{len(missing)} catalog identities missing; run catalog sync before deployment')


def sync_catalog_ratings(conn, rows):
    """The sole catalog inserter; caller owns the transaction."""
    with conn.cursor() as cur:
        cur.executemany('''INSERT INTO idol_ratings (role_id, member_name, group_name, image_url)
            VALUES (%s, %s, %s, %s) ON CONFLICT (role_id) DO UPDATE SET
            member_name=EXCLUDED.member_name, group_name=EXCLUDED.group_name,
            image_url=EXCLUDED.image_url''', rows)
    check_catalog_ratings(conn, rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    entries = json.loads((ROOT / 'static/sorter/catalog.json').read_text())['entries']
    rows = catalog_rating_rows(entries)
    if not (args.apply or args.check):
        print(f'{len(rows)} idol identities ready to sync; no database access (use --apply or --check).')
        return
    load_dotenv(ROOT / '.env')
    load_dotenv(ROOT / '.env.local', override=True)
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        if args.apply:
            sync_catalog_ratings(conn, rows)
        else:
            check_catalog_ratings(conn, rows)
    print(f'{len(rows)} identities verified; existing ratings and counts retained.')


if __name__ == '__main__':
    main()
