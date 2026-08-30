from datetime import datetime, timezone

from src.db import collections as collection_db


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.cursors = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        cursor = FakeCursor(self.responses.pop(0))
        self.cursors.append(cursor)
        return cursor


class FakePool:
    def __init__(self, connection):
        self.connection_instance = connection

    def connection(self):
        return self.connection_instance


def anchor(*, root_message_id="root-1", author_id="author-1"):
    return collection_db._Anchor(
        content_link_id=42,
        role_id="role-1",
        author_id=author_id,
        root_message_id=root_message_id,
        url="https://i.imgur.com/one.mp4",
        member_name="Hanni",
        group_name="NewJeans",
    )


def test_exact_collection_uses_root_message_and_deduplicates_urls():
    connection = FakeConnection([(42,), (43,)])

    member_ids = collection_db._exact_member_ids(connection, anchor())

    assert member_ids == [42, 43]
    assert "cl.root_message_id = %s" in connection.cursors[0].query
    assert "DISTINCT ON (cl.url)" in connection.cursors[0].query
    assert connection.cursors[0].params[:2] == ("role-1", "root-1")


def test_legacy_collection_uses_same_poster_two_minute_session():
    connection = FakeConnection([(42,), (43,), (44,)])

    member_ids = collection_db._legacy_member_ids(connection, anchor(root_message_id=None))

    assert member_ids == [42, 43, 44]
    query = connection.cursors[0].query
    assert "LAG(uploaded_date)" in query
    assert "make_interval(mins => %s)" in query
    assert "author_id = %s" in query
    assert connection.cursors[0].params[:4] == ("role-1", "author-1", 2, 42)


def test_collection_preview_returns_anchor_url_and_set_size(monkeypatch):
    connection = FakeConnection(
        [
            (
                42,
                "role-1",
                "author-1",
                "root-1",
                "https://i.imgur.com/one.mp4",
                "Hanni",
                "NewJeans",
            )
        ],
        [(42,), (43,), (44,)],
    )
    monkeypatch.setattr(collection_db, "POOL", FakePool(connection))

    preview = collection_db.get_collection_preview(42)

    assert preview == collection_db.CollectionPreview(
        url="https://i.imgur.com/one.mp4",
        count=3,
    )


def test_collection_feed_batches_exact_parent_sets(monkeypatch):
    connection = FakeConnection(
        [
            (
                42,
                "role-1",
                "author-1",
                "root-1",
                "https://i.imgur.com/one.mp4",
                "Hanni",
                "NewJeans",
                None,
                3.0,
                1.0,
            )
        ],
        [("role-1", "root-1", 42), ("role-1", "root-1", 43)],
        [
            (42, "role-1", "Hanni", "NewJeans", "https://i.imgur.com/one.mp4", None, None, 3.0, 0, 0, 0, 0, None),
            (43, "role-1", "Hanni", "NewJeans", "https://i.imgur.com/two.mp4", None, None, 2.0, 0, 0, 0, 0, None),
        ],
    )
    monkeypatch.setattr(collection_db, "POOL", FakePool(connection))

    sets = collection_db.get_collection_feed(sort="latest", limit=1, min_age="18 year 1 month")

    assert len(sets) == 1
    assert sets[0].collection_of == 42
    assert sets[0].label == "Hanni - NewJeans"
    assert [item.content_link_id for item in sets[0].items] == [42, 43]
    assert "cl.root_message_id IS NOT NULL" in connection.cursors[0].query
    assert connection.cursors[0].params[-1] == 12
    assert "unnest(%s::text[], %s::text[])" in connection.cursors[1].query


def test_collection_feed_applies_chronological_cursor(monkeypatch):
    connection = FakeConnection([])
    monkeypatch.setattr(collection_db, "POOL", FakePool(connection))
    cursor_date = datetime(2026, 1, 1, tzinfo=timezone.utc)

    sets = collection_db.get_collection_feed(
        sort="latest",
        limit=15,
        min_age="18 year 1 month",
        cursor_date=cursor_date,
        cursor_id=42,
    )

    assert sets == []
    query = connection.cursors[0].query
    assert "(set_date, content_link_id) < (%s, %s)" in query
    assert cursor_date in connection.cursors[0].params
    assert 42 in connection.cursors[0].params
