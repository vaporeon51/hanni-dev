from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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


def candidate(*, generation: int = 0) -> content_recovery.Candidate:
    return content_recovery.Candidate(
        content_link_id=42,
        role_id="123",
        url="https://imgur.com/old123",
        original_url=None,
        recovery_generation=generation,
        num_reports=4,
        initial_reaction_count=1,
        author="tester",
        uploaded_date="2026-01-01",
    )


def prepare_candidate_pipeline(monkeypatch, tmp_path, probe_result: DiscordEmbedProbeResult):
    original_media = tmp_path / "original.mp4"
    trimmed_media = tmp_path / "trimmed.mp4"
    original_media.write_bytes(b"original")
    trimmed_media.write_bytes(b"trimmed")
    selected = candidate()
    uploaded = content_recovery.UploadedMedia(
        media_id="new123",
        url="https://i.imgur.com/new123.mp4",
        deletehash=None,
        processing_status=None,
    )

    monkeypatch.setattr(content_recovery, "start_recovery_item", lambda *_args: None)
    monkeypatch.setattr(
        content_recovery,
        "recovery_sources",
        lambda *_args: [content_recovery.RecoverySource(selected.url, 0)],
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
    monkeypatch.setattr(content_recovery, "probe_discord_embed", lambda *_args, **_kwargs: probe_result)
    return selected, uploaded, FakeImgurClient(uploaded)


def test_frames_to_drop_matches_tsuki_generation_sequence():
    assert content_recovery.frames_to_drop(1, 0) == 1
    assert content_recovery.frames_to_drop(2, 0) == 3
    assert content_recovery.frames_to_drop(3, 0) == 5
    assert content_recovery.frames_to_drop(3, 2) == 2
    with pytest.raises(ValueError):
        content_recovery.frames_to_drop(2, 2)
    with pytest.raises(ValueError):
        content_recovery.frames_to_drop(4, 0)


def test_revival_dead_on_arrival_advances_generation_and_sends_role_notice(monkeypatch, tmp_path):
    selected, uploaded, imgur_client = prepare_candidate_pipeline(
        monkeypatch,
        tmp_path,
        DiscordEmbedProbeResult(
            url="https://i.imgur.com/new123.mp4",
            status="dead",
            embed_type="article",
            error="Discord rendered the URL as an article instead of media",
        ),
    )
    recorded: list[tuple[object, ...]] = []
    notices: list[tuple[object, ...]] = []
    failures: list[tuple[object, ...]] = []
    monkeypatch.setattr(content_recovery, "record_dead_replacement", lambda *args: recorded.append(args))
    monkeypatch.setattr(content_recovery, "send_recovery_dead_link_notice", lambda *args: notices.append(args))
    monkeypatch.setattr(content_recovery, "update_recovery_item", lambda *args, **_kwargs: failures.append(args))
    connection = object()
    webhook_url = "https://discord.com/api/webhooks/2/revival-token"

    result = content_recovery.process_candidate(
        connection,
        selected,
        content_recovery.RecoveryBatchConfig(),
        object(),
        imgur_client,
        "batch-1",
        webhook_url,
    )

    assert result is None
    assert failures == []
    assert len(recorded) == 1
    assert recorded[0][0:4] == (connection, selected, uploaded.url, "batch-1")
    assert recorded[0][-1] == 1
    assert notices == [(connection, webhook_url, uploaded.url, selected.url)]


def test_delayed_discord_embed_is_accepted_like_tsuki(monkeypatch, tmp_path):
    selected, uploaded, imgur_client = prepare_candidate_pipeline(
        monkeypatch,
        tmp_path,
        DiscordEmbedProbeResult(
            url="https://i.imgur.com/new123.mp4",
            status="unknown",
            error="Discord produced no embed within 30 seconds",
            embed_pending=True,
        ),
    )
    monkeypatch.setattr(
        content_recovery,
        "update_recovery_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pending embed was marked failed")),
    )

    result = content_recovery.process_candidate(
        object(),
        selected,
        content_recovery.RecoveryBatchConfig(),
        object(),
        imgur_client,
        "batch-1",
        "https://discord.com/api/webhooks/2/revival-token",
    )

    assert isinstance(result, content_recovery.PendingRecovery)
    assert result.uploaded == uploaded
    assert result.replacement_generation == 1


def test_webhook_failure_remains_failed_instead_of_becoming_live(monkeypatch, tmp_path):
    selected, _uploaded, imgur_client = prepare_candidate_pipeline(
        monkeypatch,
        tmp_path,
        DiscordEmbedProbeResult(
            url="https://i.imgur.com/new123.mp4",
            status="unknown",
            error="Discord webhook rate limited",
        ),
    )
    failures: list[tuple[str, str]] = []

    def record_failure(_connection, _batch_id, _candidate, status, **kwargs):
        failures.append((status, kwargs["error"]))

    monkeypatch.setattr(content_recovery, "update_recovery_item", record_failure)

    result = content_recovery.process_candidate(
        object(),
        selected,
        content_recovery.RecoveryBatchConfig(),
        object(),
        imgur_client,
        "batch-1",
        "https://discord.com/api/webhooks/2/revival-token",
    )

    assert result is None
    assert failures == [("failed", "Discord revival validation failed: Discord webhook rate limited")]


def test_record_dead_replacement_tags_attempt_and_exhausts_generation_three():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (7,)
    cursor.rowcount = 1

    content_recovery.record_dead_replacement(
        connection,
        candidate(generation=2),
        "https://i.imgur.com/dead.mp4",
        "batch-1",
        "direct_imgur",
        10,
        9,
        "a" * 64,
        "dead123",
        3,
    )

    content_update, content_params = cursor.execute.call_args_list[0].args
    audit_update, audit_params = cursor.execute.call_args_list[1].args
    assert "recovery_generation = %s" in content_update
    assert "is_recovery_exhausted = %s" in content_update
    assert content_params == (3, True, "https://imgur.com/old123")
    assert "replacement_generation = %s" in audit_update
    assert audit_params[6] == 3


def test_dead_link_role_notice_matches_tsuki_format():
    assert content_recovery.dead_link_role_notice(
        "https://i.imgur.com/dead.mp4",
        ("Tsuki (Billlie)", "Hanni (NewJeans)"),
    ) == (
        "⚠️ Dead link detected: <https://i.imgur.com/dead.mp4>\n"
        "Affected roles: Tsuki (Billlie), Hanni (NewJeans)"
    )
