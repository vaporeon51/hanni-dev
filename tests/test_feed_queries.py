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
        if "eligible_count" in self.calls[-1][0]:
            return [("role-1", 20), ("role-2", 20)]
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

    for sort in ("random", "latest", "oldest", "top"):
        feed_db.get_feed_items(sort=sort, limit=1, min_age="18 year 1 month")
        query, params = pool.connection_instance.cursor_instance.calls[-1]
        assert query.count("%s") == len(params)
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
    assert "WITH role_counts AS" in query
    assert "PARTITION BY cl.role_id" in query
    assert "cl.url = ANY(%s) ASC" in query
    assert "initial_reaction_count, 0)::double precision / 3.0" in query
    assert "EXTRACT(EPOCH FROM (NOW() - cl.uploaded_date))" in query
    assert 0.05 in params
    assert 14.0 in params
    assert 90.0 in params
    assert ["https://i.imgur.com/old.mp4"] in params


def test_scroll_sampling_can_strictly_exclude_recent_history(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(feed_db, "POOL", pool)

    feed_db.get_feed_items(
        sort="random",
        limit=8,
        min_age="18 year 1 month",
        recent_urls=("https://i.imgur.com/old.mp4",),
        exclude_recent=True,
    )

    query, params = pool.connection_instance.cursor_instance.calls[-1]
    assert "NOT (cl.url = ANY(%s))" in query
    assert query.count("%s") == len(params)
    assert params.count(["https://i.imgur.com/old.mp4"]) == 2


def test_role_draws_are_uniform_but_do_not_exceed_available_links(monkeypatch):
    monkeypatch.setattr(feed_db.random, "choice", lambda roles: roles[0])

    selected = feed_db._draw_role_slots([("role-1", 2), ("role-2", 3)], limit=10)

    assert selected == ["role-1", "role-1", "role-2", "role-2", "role-2"]


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
