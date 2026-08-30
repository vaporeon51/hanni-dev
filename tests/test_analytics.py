from src.db import analytics as analytics_db


class FakeCursor:
    def __init__(self):
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
        return (7,)


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


def test_country_session_increments_one_daily_aggregate(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(analytics_db, "POOL", pool)

    count = analytics_db.record_country_session("US")

    cursor = pool.connection_instance.cursor_instance
    assert count == 7
    assert "ON CONFLICT (analytics_date, country_code)" in cursor.query
    assert "session_count = web_analytics_daily.session_count + 1" in cursor.query
    assert cursor.params == ("US",)
