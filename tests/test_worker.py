from __future__ import annotations

from contextlib import contextmanager

from src.db.dead_links import DeadLinkCandidate
from src.services.discord_embed_probe import DiscordEmbedProbeResult
from src import worker


@contextmanager
def acquired_lock(_name: str):
    yield True


def test_dead_link_worker_uses_discord_when_webhook_is_configured(monkeypatch):
    query: dict[str, object] = {}
    recorded: list[tuple[str, str, str | None]] = []
    notices: list[tuple[str, str]] = []

    def get_candidates(*, limit: int, min_interval_seconds: int, min_age: str):
        query.update(limit=limit, min_interval_seconds=min_interval_seconds, min_age=min_age)
        return [DeadLinkCandidate(url="https://i.imgur.com/abc.mp4", content_link_count=1)]

    monkeypatch.setattr(worker, "advisory_lock", acquired_lock)
    monkeypatch.setattr(worker, "DISCORD_DEAD_LINK_WEBHOOK_URL", WEBHOOK_URL := "https://discord.com/api/webhooks/1/token")
    monkeypatch.setattr(worker, "DEAD_LINK_BATCH_SIZE", 50)
    monkeypatch.setattr(worker, "DISCORD_DEAD_LINK_BATCH_SIZE", 10)
    monkeypatch.setattr(worker, "get_due_urls", get_candidates)
    monkeypatch.setattr(
        worker,
        "probe_discord_embed",
        lambda url, *, webhook_url: DiscordEmbedProbeResult(url=url, status="dead", error="article"),
    )
    def record(*, url: str, status: str, error: str | None):
        recorded.append((url, status, error))
        return 1

    monkeypatch.setattr(worker, "record_check", record)
    monkeypatch.setattr(
        worker,
        "post_discord_notice",
        lambda content, *, webhook_url: notices.append((content, webhook_url)),
    )

    summary = worker.run_dead_link_checks_once()

    assert WEBHOOK_URL.endswith("/1/token")
    assert query["limit"] == 10
    assert query["min_age"] == worker.MIN_CONTENT_AGE
    assert recorded == [("https://i.imgur.com/abc.mp4", "dead", "article")]
    assert notices == [
        (
            "⚠️ Marked dead after repeated Discord embed failures\n<https://i.imgur.com/abc.mp4>",
            WEBHOOK_URL,
        )
    ]
    assert summary == {"status": "completed", "checked": 1, "live": 0, "dead": 1, "unknown": 0}


def test_dead_link_worker_skips_without_webhook(monkeypatch):
    monkeypatch.setattr(worker, "advisory_lock", acquired_lock)
    monkeypatch.setattr(worker, "DISCORD_DEAD_LINK_WEBHOOK_URL", "")
    monkeypatch.setattr(worker, "get_due_urls", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("queried DB")))

    summary = worker.run_dead_link_checks_once()

    assert summary["status"] == "skipped"
    assert summary["checked"] == 0
    assert "not configured" in summary["reason"]
