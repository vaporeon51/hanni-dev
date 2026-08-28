"""Background jobs for ingestion, dead-link checks, and recovery."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env.local", override=True)

from src.config.constants import (  # noqa: E402
    CONTENT_RECOVERY_BATCH_SIZE,
    DEAD_LINK_BATCH_SIZE,
    DEAD_LINK_INTERVAL_SECONDS,
    DISCORD_DEAD_LINK_BATCH_SIZE,
    DISCORD_DEAD_LINK_WEBHOOK_URL,
    INGESTION_INTERVAL_SECONDS,
    MIN_CONTENT_AGE,
    RECOVERY_INTERVAL_SECONDS,
)
from src.content_recovery import RecoveryBatchConfig, dead_link_role_notice, run_recovery_batch  # noqa: E402
from src.content_update import run_incremental_update  # noqa: E402
from src.db import POOL  # noqa: E402
from src.db.dead_links import get_due_urls, record_check  # noqa: E402
from src.db.locks import advisory_lock  # noqa: E402
from src.services.discord_embed_probe import post_discord_notice, probe_discord_embed  # noqa: E402


def run_ingestion_once() -> object:
    with advisory_lock("hanni:ingestion") as acquired:
        if not acquired:
            return {"status": "skipped", "reason": "another ingestion job holds the lock"}
        return run_incremental_update()


def run_dead_link_checks_once() -> dict[str, int | str]:
    with advisory_lock("hanni:dead-link-check") as acquired:
        if not acquired:
            return {"status": "skipped", "checked": 0, "live": 0, "dead": 0, "unknown": 0}

        if not DISCORD_DEAD_LINK_WEBHOOK_URL:
            return {
                "status": "skipped",
                "reason": "DISCORD_DEAD_LINK_WEBHOOK_URL is not configured",
                "checked": 0,
                "live": 0,
                "dead": 0,
                "unknown": 0,
            }

        candidates = get_due_urls(
            limit=min(DEAD_LINK_BATCH_SIZE, DISCORD_DEAD_LINK_BATCH_SIZE),
            min_interval_seconds=DEAD_LINK_INTERVAL_SECONDS,
            min_age=MIN_CONTENT_AGE,
        )
        summary = {"status": "completed", "checked": 0, "live": 0, "dead": 0, "unknown": 0}
        # Webhook requests share a tight Discord rate-limit bucket. Keep them
        # sequential and let the probe honor Retry-After responses.
        for candidate in candidates:
            result = probe_discord_embed(candidate.url, webhook_url=DISCORD_DEAD_LINK_WEBHOOK_URL)
            transitioned_count = record_check(url=candidate.url, status=result.status, error=result.error)
            if result.status == "dead" and transitioned_count > 0:
                notice_error = post_discord_notice(
                    dead_link_role_notice(candidate.url, candidate.role_labels),
                    webhook_url=DISCORD_DEAD_LINK_WEBHOOK_URL,
                )
                if notice_error:
                    print(f"Dead-link Discord notice failed for {candidate.url}: {notice_error}", flush=True)
            summary["checked"] += 1
            summary[result.status] += 1
        return summary


def run_recovery_once() -> dict[str, object]:
    with advisory_lock("hanni:recovery") as acquired:
        if not acquired:
            return {"status": "skipped", "reason": "another recovery job holds the lock"}
        return run_recovery_batch(
            RecoveryBatchConfig(
                role_id=None,
                limit=CONTENT_RECOVERY_BATCH_SIZE,
                guild_id=os.getenv("DISCORD_CONTENT_GUILD_ID", "124767749099618304"),
                channel_id=os.getenv("DISCORD_CONTENT_CHANNEL_ID", "124767749099618304"),
                auth_env="USER_AUTH",
                max_pages=int(os.getenv("CONTENT_RECOVERY_MAX_PAGES", "0")) or None,
                history_fallback=os.getenv("CONTENT_RECOVERY_HISTORY_FALLBACK", "false").lower() in {"1", "true", "yes"},
                min_age=MIN_CONTENT_AGE,
            )
        )


def run_all_once() -> dict[str, object]:
    """Run one pass in a predictable order for a single web dyno."""

    return {
        "ingestion": run_ingestion_once(),
        "dead_links": run_dead_link_checks_once(),
        "recovery": run_recovery_once(),
    }


async def _run_blocking(name: str, function: Callable[[], object]) -> None:
    try:
        result = await asyncio.to_thread(function)
        print(f"{name}: {result}", flush=True)
    except Exception as error:  # keep the scheduler alive after one bad external call
        print(f"{name} failed: {type(error).__name__}: {error}", flush=True)


async def scheduler_loop() -> None:
    """Run jobs continuously when the app is deployed as the one-dyno version."""

    last_ingestion = 0.0
    last_dead_links = 0.0
    last_recovery = 0.0
    loop = asyncio.get_running_loop()
    while True:
        now = loop.time()
        jobs: list[tuple[str, Callable[[], object]]] = []
        if now - last_ingestion >= INGESTION_INTERVAL_SECONDS:
            jobs.append(("ingestion", run_ingestion_once))
            last_ingestion = now
        if now - last_dead_links >= DEAD_LINK_INTERVAL_SECONDS:
            jobs.append(("dead-link checks", run_dead_link_checks_once))
            last_dead_links = now
        if now - last_recovery >= RECOVERY_INTERVAL_SECONDS:
            jobs.append(("recovery", run_recovery_once))
            last_recovery = now
        for name, function in jobs:
            await _run_blocking(name, function)
        await asyncio.sleep(5)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Hanni background jobs")
    parser.add_argument("job", choices=("ingest", "dead-links", "recovery", "all", "scheduler"), default="all", nargs="?")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    POOL.open()
    try:
        if args.job == "scheduler":
            asyncio.run(scheduler_loop())
        elif args.job == "ingest":
            print(run_ingestion_once())
        elif args.job == "dead-links":
            print(run_dead_link_checks_once())
        elif args.job == "recovery":
            print(run_recovery_once())
        else:
            print(run_all_once())
    finally:
        POOL.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
