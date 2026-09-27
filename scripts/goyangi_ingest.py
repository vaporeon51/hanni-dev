#!/usr/bin/env python3
"""Local CLI wrapper for the goyangi second-source ingest (dry run unless --apply)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.db import POOL  # noqa: E402
from src.goyangi_ingest import run_pass, sweep_forward_race  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Goyangi ingest (dry run unless --apply)")
    parser.add_argument("--since", default=None)
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--max-sets", type=int, default=1000)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sweep", action="store_true", help="forward-race sweep instead of ingest")
    args = parser.parse_args(argv)

    POOL.open()
    try:
        if args.sweep:
            result = sweep_forward_race(apply=args.apply)
            print(f"sweep candidates: {result['candidates']} deleted: {result['deleted']}")
            for viewer_url, keep_url in result["pairs"]:
                print(f"  would-remove {viewer_url}\n    keep {keep_url}")
            return 0
        summary = run_pass(since=args.since, per_page=args.per_page,
                           max_sets=args.max_sets, apply=args.apply)
        print(f"sets={summary['sets']} contents={summary['contents']} "
              f"inserted={summary['inserted']} skipped={summary['skipped']}")
        for decision, count in sorted(summary["decisions"].items(), key=lambda kv: -kv[1]):
            print(f"{count:6d}  {decision}")
        print("high_watermark:", summary["high_watermark"])
        if not args.apply:
            print("dry run: no writes (pass --apply to insert)")
    finally:
        POOL.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
