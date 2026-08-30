from __future__ import annotations

from datetime import datetime, timezone

from src import content_backfill
from src.content_ingestion import ContentLinkDraft
from src.db import content_backfill as backfill_db
from src.services.content_backfill import (
    BackfillStats,
    ReconciliationRow,
    media_identity,
    plan_reconciliation,
)


UPLOADED = datetime(2023, 3, 13, 4, 26, tzinfo=timezone.utc)


def draft(*, kind="root", url="https://imgur.com/AbC123x", message_id="101"):
    return ContentLinkDraft(
        role_id="role-1",
        author_id="author-1",
        author="poster",
        uploaded_date=UPLOADED,
        url=url,
        source_message_id=message_id,
        root_message_id="100",
        source_kind=kind,
        initial_reaction_count=5,
    )


def row(
    row_id,
    *,
    url="https://i.imgur.com/AbC123x.mp4",
    original_url=None,
    source_message_id=None,
):
    return ReconciliationRow(
        content_link_id=row_id,
        role_id="role-1",
        author_id="author-1",
        uploaded_date=datetime(2023, 3, 13, 0, 26),
        url=url,
        original_url=original_url,
        source_message_id=source_message_id,
    )


def test_media_identity_matches_imgur_page_direct_and_album_slug_forms():
    assert media_identity("https://imgur.com/AbC123x") == media_identity(
        "https://i.imgur.com/AbC123x.mp4?cache=1"
    )
    assert media_identity("https://imgur.com/a/karina-x-aespa-capo-ZHB76tL") == (
        "imgur",
        "ZHB76tL",
    )


def test_unambiguous_legacy_row_is_updated_in_place():
    decisions = plan_reconciliation([draft()], [row(42)])

    assert decisions[0].action == "update"
    assert decisions[0].content_link_id == 42


def test_naive_utc_historical_row_is_also_updated_in_place():
    utc_row = row(42)
    utc_row = ReconciliationRow(
        content_link_id=utc_row.content_link_id,
        role_id=utc_row.role_id,
        author_id=utc_row.author_id,
        uploaded_date=UPLOADED.replace(tzinfo=None),
        url=utc_row.url,
        original_url=utc_row.original_url,
        source_message_id=utc_row.source_message_id,
    )

    decisions = plan_reconciliation([draft()], [utc_row])

    assert decisions[0].action == "update"


def test_recovered_legacy_row_matches_its_original_url():
    decisions = plan_reconciliation(
        [draft()],
        [row(42, url="https://i.imgur.com/New999x.mp4", original_url="https://imgur.com/AbC123x")],
    )

    assert decisions[0].action == "update"
    assert decisions[0].content_link_id == 42


def test_existing_provenance_makes_replay_idempotent():
    decisions = plan_reconciliation([draft()], [row(42, source_message_id="101")])

    assert decisions[0].action == "existing"


def test_ambiguous_legacy_rows_are_not_guessed_or_inserted():
    decisions = plan_reconciliation([draft()], [row(42), row(43)])

    assert decisions[0].action == "ambiguous"
    assert decisions[0].content_link_id is None


def test_missing_children_insert_but_missing_roots_do_not_duplicate():
    decisions = plan_reconciliation(
        [draft(kind="root", message_id="100"), draft(kind="reply_continuation", message_id="101")],
        [],
    )

    assert [decision.action for decision in decisions] == ["unmatched_root", "insert"]


def test_backfill_start_snowflake_is_monotonic():
    earlier = backfill_db.datetime_to_discord_snowflake(datetime(2023, 1, 1, tzinfo=timezone.utc))
    later = backfill_db.datetime_to_discord_snowflake(datetime(2023, 1, 2, tzinfo=timezone.utc))

    assert int(earlier) < int(later)


def test_default_backfill_boundary_starts_at_2025_utc():
    start_utc = backfill_db.DEFAULT_BACKFILL_START_UTC

    assert start_utc == datetime(2025, 1, 1, tzinfo=timezone.utc)


def test_dry_run_replays_chronologically_without_apply(monkeypatch, tmp_path, capsys):
    bounds = backfill_db.BackfillBounds(start_message_id="99", end_message_id="200")
    messages = [
        {
            "id": "100",
            "timestamp": "2023-03-13T00:26:00+00:00",
            "content": "",
            "mention_roles": ["role-1"],
            "author": {"id": "author-1", "username": "poster"},
            "embeds": [{"type": "video", "url": "https://imgur.com/AbC123x"}],
        },
        {
            "id": "101",
            "timestamp": "2023-03-13T00:26:30+00:00",
            "content": "https://imgur.com/Def456y",
            "mention_roles": [],
            "author": {"id": "author-1", "username": "poster"},
            "embeds": [{"type": "video", "url": "https://imgur.com/Def456y"}],
        },
    ]
    captured = {}
    monkeypatch.setattr(content_backfill.backfill_db, "get_default_bounds", lambda: bounds)
    monkeypatch.setattr(content_backfill, "get_known_role_ids", lambda: frozenset({"role-1"}))
    monkeypatch.setattr(content_backfill, "_prime_classifier", lambda *_args: None)
    monkeypatch.setattr(content_backfill.content_discord, "get_messages_after", lambda _cursor: list(reversed(messages)))

    def fake_reconcile(links, **kwargs):
        captured["links"] = links
        captured.update(kwargs)
        return BackfillStats(messages_scanned=2, links_classified=2, unmatched_roots=1, inserted_links=1)

    monkeypatch.setattr(content_backfill.backfill_db, "reconcile_page", fake_reconcile)

    result = content_backfill.run_historical_backfill(
        content_backfill.BackfillConfig(
            max_pages=1,
            request_delay_seconds=0,
            checkpoint_path=tmp_path / "unused.json",
        )
    )

    assert [link.source_message_id for link in captured["links"]] == ["100", "101"]
    assert captured["apply"] is False
    assert result["status"] == "paused"
    assert result["inserted_links"] == 1
    assert not (tmp_path / "unused.json").exists()
    assert "through=2023-03-13 00:26 UTC" in capsys.readouterr().out


def test_apply_mode_resumes_from_local_checkpoint(monkeypatch, tmp_path):
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint = content_backfill.LocalCheckpoint(
        start_message_id="50",
        end_message_id="200",
        last_message_id="150",
        stats=BackfillStats(messages_scanned=100),
    )
    content_backfill._write_checkpoint(checkpoint_path, checkpoint)
    monkeypatch.setattr(
        content_backfill.backfill_db,
        "get_default_bounds",
        lambda: (_ for _ in ()).throw(AssertionError("resume should use the local checkpoint")),
    )
    monkeypatch.setattr(content_backfill, "get_known_role_ids", lambda: frozenset())
    primed = []
    monkeypatch.setattr(content_backfill, "_prime_classifier", lambda _classifier, cursor: primed.append(cursor))
    monkeypatch.setattr(content_backfill.content_discord, "get_messages_after", lambda _cursor: [])

    result = content_backfill.run_historical_backfill(
        content_backfill.BackfillConfig(
            apply=True,
            request_delay_seconds=0,
            checkpoint_path=checkpoint_path,
        )
    )

    saved = content_backfill._load_checkpoint(checkpoint_path)
    assert saved is not None
    assert saved.status == "completed"
    assert saved.last_message_id == "150"
    assert primed == ["150"]
    assert result["last_message_id"] == "150"
    assert result["status"] == "completed"


def test_checkpoint_round_trip_is_atomic_and_preserves_stats(tmp_path):
    checkpoint_path = tmp_path / "state.json"
    checkpoint = content_backfill.LocalCheckpoint(
        start_message_id="10",
        end_message_id="30",
        last_message_id="20",
        stats=BackfillStats(messages_scanned=100, inserted_links=4),
    )

    content_backfill._write_checkpoint(checkpoint_path, checkpoint)

    assert content_backfill._load_checkpoint(checkpoint_path) == checkpoint
    assert not checkpoint_path.with_name("state.json.tmp").exists()
