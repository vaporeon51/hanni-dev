from __future__ import annotations

from src import content_recovery
from src.services.discord_embed_probe import DiscordEmbedProbeResult


class FakeImgurClient:
    def __init__(self, uploaded: content_recovery.UploadedMedia):
        self.uploaded = uploaded
        self.verified: list[str] = []

    def upload(self, _path):
        return self.uploaded

    def verify_direct_url(self, url: str):
        self.verified.append(url)


def test_revival_dead_on_arrival_is_logged_and_not_committed(monkeypatch, tmp_path):
    original_media = tmp_path / "original.mp4"
    trimmed_media = tmp_path / "trimmed.mp4"
    original_media.write_bytes(b"original")
    trimmed_media.write_bytes(b"trimmed")

    candidate = content_recovery.Candidate(
        content_link_id=42,
        role_id="123",
        url="https://imgur.com/old123",
        original_url=None,
        recovery_generation=0,
        num_reports=0,
        initial_reaction_count=1,
        author="tester",
        uploaded_date="2026-01-01",
    )
    uploaded = content_recovery.UploadedMedia(
        media_id="new123",
        url="https://i.imgur.com/new123.mp4",
        deletehash=None,
        processing_status=None,
    )
    failures: list[tuple[str, str]] = []
    notices: list[tuple[str, str]] = []

    monkeypatch.setattr(content_recovery, "start_recovery_item", lambda *_args: None)
    monkeypatch.setattr(
        content_recovery,
        "recovery_sources",
        lambda *_args: [content_recovery.RecoverySource(candidate.url, 0)],
    )
    monkeypatch.setattr(
        content_recovery,
        "recover_content",
        lambda *_args, **_kwargs: content_recovery.DownloadedMedia(
            path=original_media,
            content_type="video/mp4",
            size=original_media.stat().st_size,
        ),
    )
    monkeypatch.setattr(content_recovery, "trim_leading_frames", lambda *_args: trimmed_media)
    monkeypatch.setattr(content_recovery, "sha256", lambda _path: "abc123")
    monkeypatch.setattr(
        content_recovery,
        "probe_discord_embed",
        lambda url, *, webhook_url: DiscordEmbedProbeResult(
            url=url,
            status="dead",
            embed_type="article",
            error="Discord rendered the URL as an article instead of media",
        ),
    )

    def record_failure(_connection, _batch_id, _candidate, status, **kwargs):
        failures.append((status, kwargs["error"]))

    monkeypatch.setattr(content_recovery, "update_recovery_item", record_failure)
    monkeypatch.setattr(
        content_recovery,
        "post_discord_notice",
        lambda content, *, webhook_url: notices.append((content, webhook_url)),
    )

    result = content_recovery.process_candidate(
        object(),
        candidate,
        content_recovery.RecoveryBatchConfig(),
        object(),
        FakeImgurClient(uploaded),
        "batch-1",
        "https://discord.com/api/webhooks/2/revival-token",
    )

    assert result is None
    assert failures == [
        (
            "dead",
            "Discord revival validation was dead: Discord rendered the URL as an article instead of media",
        )
    ]
    assert notices == [
        (
            "⚠️ Revival dead; database not updated\n<https://i.imgur.com/new123.mp4>",
            "https://discord.com/api/webhooks/2/revival-token",
        )
    ]
