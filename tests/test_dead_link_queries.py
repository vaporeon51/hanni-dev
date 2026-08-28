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

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.query = str(query)
        self.params = params

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
    cursor = CapturingCursor([])
    monkeypatch.setattr(dead_links, "POOL", FakePool(FakeConnection(cursor)))

    rows = dead_links.get_due_urls(limit=10, min_interval_seconds=300, min_age="21 years")

    assert rows == []
    assert "JOIN role_info AS ri ON ri.role_id = cl.role_id" in cursor.query
    assert "cl.uploaded_date > ri.birthday + %s::interval" in cursor.query
    assert cursor.params == (
        REPORT_THRESHOLD,
        "21 years",
        *(f"%://{host}/%" for host in sorted(EPHEMERAL_MEDIA_HOSTS)),
        300,
        10,
    )


def test_recovery_candidates_apply_minimum_age():
    cursor = CapturingCursor([])
    connection = FakeConnection(cursor)

    rows = content_recovery.fetch_candidates(connection, None, 25, "19 years 6 months")

    assert rows == []
    assert "JOIN role_info AS ri ON ri.role_id = cl.role_id" in cursor.query
    assert "cl.uploaded_date > ri.birthday + %s::interval" in cursor.query
    assert cursor.params == [CONTENT_RECOVERY_MAX_GENERATION, "19 years 6 months", 25]
