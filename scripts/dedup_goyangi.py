#!/usr/bin/env python3
"""Find/remove duplicate Goyangi sets while retaining Discord rows."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / '.env')
from src.db import POOL  # noqa: E402
from src.services.goyangi_cleanup import cleanup  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='archive and remove matched Goyangi rows')
    parser.add_argument('--exact-only', action='store_true',
                        help='use stored identities only; skip API hydration and video fingerprints')
    parser.add_argument('--allow-visual-delete', action='store_true',
                        help='allow fingerprint matches to archive and remove Goyangi sets')
    parser.add_argument('--limit', type=int, default=10000)
    parser.add_argument('--report', type=Path, help='write full JSON report')
    args = parser.parse_args(argv)
    POOL.open()
    try:
        result = cleanup(apply=args.apply, confirmed=True,
                         verify_media=not args.exact_only, limit=args.limit,
                         allow_visual=args.allow_visual_delete)
    finally:
        POOL.close()
    encoded = json.dumps(result, indent=2, default=str)
    print(encoded)
    if args.report:
        args.report.write_text(encoded + '\n')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
