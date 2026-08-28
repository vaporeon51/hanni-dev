from __future__ import annotations

from contextlib import contextmanager

from src.config.constants import CONTENT_RECOVERY_MAX_GENERATION, EPHEMERAL_MEDIA_HOSTS, REPORT_THRESHOLD
from src import content_recovery
from src.db import dead_links


class CapturingCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.query = ""
        self.params = None
        self.calls = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.query = str(query)
        self.params = params
        self.calls.append((self.query, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor: CapturingCursor):
        self.captured_cursor = cursor

    def cursor(self, **_kwargs):
        return self.captured_cursor


class FakePool:
    def __init__(self, connection: FakeConnection):
        self.fake_connection = connection

    @contextmanager
    def connection(self):
        yield self.fake_connection


def test_dead_link_candidates_apply_minimum_age(monkeypatch):
    cursor = CapturingCursor(
        [("https://i.imgur.com/shared.mp4", 2, ["Hanni (NewJeans)", "Minji (NewJeans)"])]
    )
    monkeypatch.setattr(dead_links, "POOL", FakePool(FakeConnection(cursor)))

    rows = dead_links.get_due_urls(limit=10, min_interval_seconds=300, min_age="21 years")

    assert rows == [
        dead_links.DeadLinkCandidate(
            url="https://i.imgur.com/shared.mp4",
            content_link_count=2,
            role_labels=("Hanni (NewJeans)", "Minji (NewJeans)"),
        )
    ]
    assert "JOIN role_info AS ri ON ri.role_id = cl.role_id" in cursor.query
    assert "cl.uploaded_date > ri.birthday + %s::interval" in cursor.query
    assert "array_agg(DISTINCT role_label ORDER BY role_label)" in cursor.query
    assert cursor.params == (
        REPORT_THRESHOLD,
        "21 years",
        *(f"%://{host}/%" for host in sorted(EPHEMERAL_MEDIA_HOSTS)),
        300,
        10,
    )


def test_confirmed_dead_check_marks_every_row_for_the_url(monkeypatch):
    cursor = CapturingCursor()
    cursor.rowcount = 3
    monkeypatch.setattr(dead_links, "POOL", FakePool(FakeConnection(cursor)))

    transitioned = dead_links.record_check(
        url="https://i.imgur.com/shared.mp4",
        status="dead",
        error="article",
    )

    assert transitioned == 3
    assert len(cursor.calls) == 2
    increment_query, increment_params = cursor.calls[0]
    transition_query, transition_params = cursor.calls[1]
    assert "dead_check_failures = dead_check_failures + 1" in increment_query
    assert increment_params == ("article", "https://i.imgur.com/shared.mp4", REPORT_THRESHOLD)
    assert "EXISTS" not in transition_query
    assert "dead_check_failures" not in transition_query
    assert "num_reports" not in transition_query
    assert transition_params == (
        CONTENT_RECOVERY_MAX_GENERATION,
        "https://i.imgur.com/shared.mp4",
    )


def test_priority_candidates_preserve_queue_order(monkeypatch):
    cursor = CapturingCursor(
        [
            ("https://i.imgur.com/two.mp4", 1, ["Minji (NewJeans)"], 1),
            ("https://i.imgur.com/one.mp4", 1, ["Hanni (NewJeans)"], 2),
        ]
    )
    monkeypatch.setattr(dead_links, "POOL", FakePool(FakeConnection(cursor)))

    rows = dead_links.get_candidates_by_urls(
        ["https://i.imgur.com/two.mp4", "https://i.imgur.com/one.mp4"],
        min_age="21 years",
    )

    assert [row.url for row in rows] == [
        "https://i.imgur.com/two.mp4",
        "https://i.imgur.com/one.mp4",
    ]
    assert "WITH ORDINALITY" in cursor.query
    assert "ORDER BY position" in cursor.query
    assert cursor.params == (
        ["https://i.imgur.com/two.mp4", "https://i.imgur.com/one.mp4"],
        REPORT_THRESHOLD,
        "21 years",
        *(f"%://{host}/%" for host in sorted(EPHEMERAL_MEDIA_HOSTS)),
    )


def test_recovery_candidates_apply_minimum_age():
    cursor = CapturingCursor([])
    connection = FakeConnection(cursor)

    rows = content_recovery.fetch_candidates(connection, None, 25, "19 years 6 months")

    assert rows == []
    assert "JOIN role_info AS ri ON ri.role_id = cl.role_id" in cursor.query
    assert "cl.uploaded_date > ri.birthday + %s::interval" in cursor.query
    assert cursor.params == [CONTENT_RECOVERY_MAX_GENERATION, "19 years 6 months", 25]
