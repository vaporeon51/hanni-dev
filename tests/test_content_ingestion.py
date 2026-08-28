from __future__ import annotations

from src.content_ingestion import ContentMessageClassifier


def message(message_id: str, timestamp: str, *, content: str = "", roles=None, embeds=None, reference=None):
    return {
        "id": message_id,
        "timestamp": timestamp,
        "content": content,
        "mention_roles": roles or [],
        "author": {"id": "author-1", "username": "poster"},
        "embeds": embeds or [],
        "message_reference": reference,
    }


def test_root_and_reply_continuation_are_ingested():
    classifier = ContentMessageClassifier()
    root = message(
        "100",
        "2026-01-01T00:00:00+00:00",
        roles=["role-1"],
        embeds=[{"type": "video", "url": "https://i.imgur.com/one.mp4"}],
    )
    reply = message(
        "101",
        "2026-01-01T00:01:00+00:00",
        embeds=[{"type": "video", "url": "https://i.imgur.com/two.mp4"}],
        reference={"message_id": "100"},
    )

    root_links = classifier.consume(root)
    reply_links = classifier.consume(reply)

    assert root_links[0].role_id == "role-1"
    assert root_links[0].source_kind == "root"
    assert reply_links[0].role_id == "role-1"
    assert reply_links[0].source_kind == "reply_continuation"


def test_unrelated_media_message_is_not_attributed_to_previous_role():
    classifier = ContentMessageClassifier()
    classifier.consume(
        message(
            "100",
            "2026-01-01T00:00:00+00:00",
            roles=["role-1"],
            embeds=[{"type": "video", "url": "https://i.imgur.com/one.mp4"}],
        )
    )
    unrelated = message(
        "101",
        "2026-01-01T00:10:00+00:00",
        embeds=[{"type": "video", "url": "https://i.imgur.com/two.mp4"}],
    )

    assert classifier.consume(unrelated) == []
