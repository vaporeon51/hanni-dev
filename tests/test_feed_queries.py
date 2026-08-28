from __future__ import annotations

from datetime import datetime, timezone

from src.config.constants import EPHEMERAL_MEDIA_HOSTS
from src.db import feed as feed_db


class FakeCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params):
        self.calls.append((query, params))

    def fetchall(self):
        return [
            (
                1,
                "role-1",
                "Hanni",
                "NewJeans",
                "https://i.imgur.com/a.png",
                None,
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                4.0,
                2,
                1,
                0,
                0,
                None,
                0.5,
            )
        ]


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cursor_instance


class FakePool:
    def __init__(self):
        self.connection_instance = FakeConnection()

    def connection(self):
        return self.connection_instance


def test_feed_sql_has_a_parameter_for_each_placeholder(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(feed_db, "POOL", pool)

    for sort, expected_select_parameters in (("random", 3), ("latest", 1), ("oldest", 1), ("top", 1)):
        feed_db.get_feed_items(sort=sort, limit=1, min_age="18 year 1 month")
        query, params = pool.connection_instance.cursor_instance.calls[-1]
        assert query.count("%s") == len(params)
        assert len(params) == expected_select_parameters + 3 + len(EPHEMERAL_MEDIA_HOSTS)
        for host in EPHEMERAL_MEDIA_HOSTS:
            assert f"%://{host}/%" in params


def test_random_feed_prioritizes_urls_outside_recent_history(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(feed_db, "POOL", pool)

    feed_db.get_feed_items(
        sort="random",
        limit=15,
        min_age="18 year 1 month",
        recent_urls=("https://i.imgur.com/old.mp4",),
    )

    query, params = pool.connection_instance.cursor_instance.calls[-1]
    assert query.count("%s") == len(params)
    assert "cl.url = ANY(%s) AS recently_seen" in query
    assert "ORDER BY recently_seen ASC, random_weight DESC" in query
    assert ["https://i.imgur.com/old.mp4"] in params


def test_feed_label_collapses_solo_artist_duplicate():
    item = feed_db.FeedItem(
        content_link_id=1,
        role_id="iu-role",
        member_name="IU",
        group_name="iu",
        url="https://i.imgur.com/iu.png",
        original_url=None,
        uploaded_date=None,
        score=0,
    )

    assert item.label == "IU"


def test_feed_label_uses_whichever_name_is_available():
    member_only = feed_db.FeedItem(
        content_link_id=1,
        role_id="member-role",
        member_name="BIBI",
        group_name=None,
        url="https://i.imgur.com/bibi.png",
        original_url=None,
        uploaded_date=None,
        score=0,
    )
    group_only = feed_db.FeedItem(
        content_link_id=2,
        role_id="group-role",
        member_name=None,
        group_name="TWICE",
        url="https://i.imgur.com/twice.png",
        original_url=None,
        uploaded_date=None,
        score=0,
    )

    assert member_only.label == "BIBI"
    assert group_only.label == "TWICE"


def test_role_search_uses_tokenized_best_match(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(feed_db, "POOL", pool)

    role_ids = feed_db._role_ids_for_query(
        pool.connection_instance,
        "chaewon sserafim",
        "18 year 1 month",
    )

    query, params = pool.connection_instance.cursor_instance.calls[-1]
    assert role_ids == ["1"]
    assert "string_to_array" in query
    assert "regexp_replace" in query
    assert "unnest(member_group_array)" in query
    assert "maxmatches" in query
    assert params == ("chaewon sserafim", "18 year 1 month")
