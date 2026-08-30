"""Resumable one-time replay of historical Discord content messages."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env.local", override=True)

from src import content_discord, content_ingestion  # noqa: E402
from src.db import POOL  # noqa: E402
from src.db import content_backfill as backfill_db  # noqa: E402
from src.db.content_update import get_known_role_ids  # noqa: E402
from src.db.locks import advisory_lock  # noqa: E402
from src.services.content_backfill import BackfillStats  # noqa: E402

DEFAULT_CHECKPOINT_PATH = REPO_ROOT / ".content-backfill-state.json"


@dataclass(frozen=True)
class BackfillConfig:
    apply: bool = False
    max_pages: int | None = None
    after_message_id: str | None = None
    end_message_id: str | None = None
    request_delay_seconds: float = content_discord.REQUEST_DELAY_SECONDS
    checkpoint_path: Path = DEFAULT_CHECKPOINT_PATH
    verbose: bool = False


@dataclass(frozen=True)
class LocalCheckpoint:
    start_message_id: str
    end_message_id: str
    last_message_id: str
    status: str = "running"
    stats: BackfillStats = BackfillStats()

    def as_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "start_message_id": self.start_message_id,
            "end_message_id": self.end_message_id,
            "last_message_id": self.last_message_id,
            "status": self.status,
            "stats": self.stats.as_dict(),
        }


def _load_checkpoint(path: Path) -> LocalCheckpoint | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or payload.get("status") not in {"running", "completed"}:
            raise ValueError("unsupported checkpoint format")
        message_ids = [
            str(payload[key])
            for key in ("start_message_id", "end_message_id", "last_message_id")
        ]
        if not all(value.isdigit() for value in message_ids):
            raise ValueError("checkpoint message IDs must contain digits only")
        return LocalCheckpoint(
            start_message_id=message_ids[0],
            end_message_id=message_ids[1],
            last_message_id=message_ids[2],
            status=str(payload["status"]),
            stats=BackfillStats(**payload.get("stats", {})),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Invalid historical backfill checkpoint at {path}: {error}") from error


def _write_checkpoint(path: Path, checkpoint: LocalCheckpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(checkpoint.as_dict(), file, indent=2, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    temporary_path.replace(path)


def _prime_classifier(classifier: content_ingestion.ContentMessageClassifier, message_id: str) -> None:
    context_messages = content_discord.get_messages_around(message_id)
    for message in sorted(context_messages, key=lambda item: int(item["id"])):
        if int(message["id"]) <= int(message_id):
            classifier.consume(message)


def _format_through_date(message: dict[str, object]) -> str:
    raw_timestamp = message.get("timestamp")
    if not isinstance(raw_timestamp, str):
        return "unknown"
    try:
        timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return raw_timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def run_historical_backfill(config: BackfillConfig) -> dict[str, object]:
    if config.apply:
        if config.after_message_id or config.end_message_id:
            raise ValueError("Message-ID overrides are dry-run only; apply mode uses its local checkpoint")
        checkpoint = _load_checkpoint(config.checkpoint_path)
        if checkpoint is None:
            bounds = backfill_db.get_default_bounds()
            if bounds is None:
                return {"status": "completed", "reason": "no legacy content rows remain"}
            checkpoint = LocalCheckpoint(
                start_message_id=bounds.start_message_id,
                end_message_id=bounds.end_message_id,
                last_message_id=bounds.start_message_id,
            )
            _write_checkpoint(config.checkpoint_path, checkpoint)
        if checkpoint.status == "completed":
            return {"status": "completed", "mode": "apply", **checkpoint.stats.as_dict()}
        cursor_message_id = checkpoint.last_message_id
        end_message_id = checkpoint.end_message_id
    else:
        bounds = backfill_db.get_default_bounds()
        if bounds is None:
            return {"status": "completed", "reason": "no legacy content rows remain"}
        checkpoint = None
        cursor_message_id = config.after_message_id or bounds.start_message_id
        end_message_id = config.end_message_id or bounds.end_message_id

    if int(cursor_message_id) >= int(end_message_id):
        if checkpoint is not None:
            checkpoint = replace(checkpoint, status="completed")
            _write_checkpoint(config.checkpoint_path, checkpoint)
        return {"status": "completed", "mode": "apply" if config.apply else "dry-run"}

    classifier = content_ingestion.ContentMessageClassifier(fallback_role_ids=get_known_role_ids())
    _prime_classifier(classifier, cursor_message_id)

    totals = BackfillStats()
    pages = 0
    completed = False
    while config.max_pages is None or pages < config.max_pages:
        messages = content_discord.get_messages_after(cursor_message_id)
        if not messages:
            completed = True
            break
        messages.sort(key=lambda item: int(item["id"]))
        eligible = [message for message in messages if int(cursor_message_id) < int(message["id"]) < int(end_message_id)]
        reached_end = any(int(message["id"]) >= int(end_message_id) for message in messages)
        if not eligible:
            if reached_end:
                completed = True
                break
            raise RuntimeError("Discord history pagination did not advance")

        page_links: list[content_ingestion.ContentLinkDraft] = []
        for message in eligible:
            page_links.extend(classifier.consume(message))
        cursor_message_id = str(eligible[-1]["id"])
        page_stats = backfill_db.reconcile_page(
            page_links,
            messages_scanned=len(eligible),
            apply=config.apply,
            verbose=config.verbose,
        )
        totals += page_stats
        if checkpoint is not None:
            checkpoint = replace(
                checkpoint,
                last_message_id=cursor_message_id,
                stats=checkpoint.stats + page_stats,
            )
            _write_checkpoint(config.checkpoint_path, checkpoint)
        pages += 1
        mode = "applied" if config.apply else "would apply"
        through_date = _format_through_date(eligible[-1])
        print(
            f"Historical page {pages:,}: through={through_date} "
            f"messages={page_stats.messages_scanned:,} "
            f"classified={page_stats.links_classified:,} matched={page_stats.matched_legacy_links:,} "
            f"insert={page_stats.inserted_links:,} ambiguous={page_stats.ambiguous_links:,} ({mode})",
            flush=True,
        )
        if reached_end:
            completed = True
            break
        if config.request_delay_seconds > 0:
            time.sleep(config.request_delay_seconds)

    if completed and checkpoint is not None:
        checkpoint = replace(checkpoint, status="completed")
        _write_checkpoint(config.checkpoint_path, checkpoint)

    result: dict[str, object] = {
        "status": "completed" if completed else "paused",
        "mode": "apply" if config.apply else "dry-run",
        "pages": pages,
        "last_message_id": cursor_message_id,
        **totals.as_dict(),
    }
    if checkpoint is not None:
        result["checkpoint"] = str(config.checkpoint_path)
        result["cumulative"] = checkpoint.stats.as_dict()
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay legacy Discord history and recover missing content children")
    parser.add_argument("--apply", action="store_true", help="Commit reconciled roots, children, and progress")
    parser.add_argument("--verbose", action="store_true", help="Show noteworthy per-link dry-run decisions")
    parser.add_argument("--max-pages", type=int, help="Stop after this many 100-message history pages")
    parser.add_argument("--after-message-id", help="Dry-run override for the first Discord cursor")
    parser.add_argument("--end-message-id", help="Dry-run override for the exclusive Discord end cursor")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=DEFAULT_CHECKPOINT_PATH,
        help=f"Apply-mode checkpoint path (default: {DEFAULT_CHECKPOINT_PATH.name})",
    )
    args = parser.parse_args(argv)
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    if args.apply and (args.after_message_id or args.end_message_id):
        parser.error("message-ID overrides cannot be used with --apply")
    for value in (args.after_message_id, args.end_message_id):
        if value is not None and not value.isdigit():
            parser.error("message IDs must contain digits only")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    POOL.open()
    try:
        with advisory_lock("hanni:historical-content-backfill") as acquired:
            if not acquired:
                print({"status": "skipped", "reason": "another historical backfill holds the lock"})
                return 0
            result = run_historical_backfill(
                BackfillConfig(
                    apply=args.apply,
                    max_pages=args.max_pages,
                    after_message_id=args.after_message_id,
                    end_message_id=args.end_message_id,
                    checkpoint_path=args.checkpoint,
                    verbose=args.verbose,
                )
            )
            print(result)
    finally:
        POOL.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
