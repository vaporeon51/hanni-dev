from __future__ import annotations

import requests

from src.services.discord_embed_probe import post_discord_notice, probe_discord_embed


WEBHOOK_URL = "https://discord.com/api/webhooks/123456/very-secret-token"
MEDIA_URL = "https://i.imgur.com/GTIU8p5.mp4"


class FakeResponse:
    def __init__(self, status_code: int, payload: object | None = None, *, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}
        self.closed = False

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]):
        self.responses = iter(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: object):
        self.calls.append((method, url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds: float):
        self.now += seconds


def test_article_embed_is_dead_and_probe_message_is_retained():
    session = FakeSession(
        [
            FakeResponse(200, {"id": "999", "embeds": [{"type": "article"}]}),
        ]
    )

    result = probe_discord_embed(MEDIA_URL, webhook_url=WEBHOOK_URL, session=session)

    assert result.status == "dead"
    assert result.embed_type == "article"
    assert [call[0] for call in session.calls] == ["POST"]
    assert session.calls[0][1].endswith("very-secret-token?wait=true")
    assert session.calls[0][2]["json"] == {"content": MEDIA_URL, "allowed_mentions": {"parse": []}}


def test_media_embed_is_live():
    session = FakeSession(
        [
            FakeResponse(200, {"id": "999", "embeds": [{"type": "video"}]}),
        ]
    )

    result = probe_discord_embed(MEDIA_URL, webhook_url=WEBHOOK_URL, session=session)

    assert result.status == "live"
    assert result.embed_type == "video"


def test_probe_polls_until_discord_adds_an_embed():
    clock = FakeClock()
    session = FakeSession(
        [
            FakeResponse(200, {"id": "999", "embeds": []}),
            FakeResponse(200, {"id": "999", "embeds": [{"type": "gifv"}]}),
        ]
    )

    result = probe_discord_embed(
        MEDIA_URL,
        webhook_url=WEBHOOK_URL,
        session=session,
        wait_seconds=30,
        poll_interval_seconds=2,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result.status == "live"
    assert [call[0] for call in session.calls] == ["POST", "GET"]
    assert clock.now == 2


def test_no_embed_is_unknown_not_dead():
    clock = FakeClock()
    session = FakeSession(
        [
            FakeResponse(200, {"id": "999", "embeds": []}),
            FakeResponse(200, {"id": "999", "embeds": []}),
            FakeResponse(200, {"id": "999", "embeds": []}),
        ]
    )

    result = probe_discord_embed(
        MEDIA_URL,
        webhook_url=WEBHOOK_URL,
        session=session,
        wait_seconds=4,
        poll_interval_seconds=2,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result.status == "unknown"
    assert result.embed_pending is True
    assert result.error == "Discord produced no embed within 4 seconds"
    assert [call[0] for call in session.calls] == ["POST", "GET", "GET"]


def test_request_errors_do_not_expose_webhook_token():
    session = FakeSession([requests.ConnectionError(f"could not connect to {WEBHOOK_URL}")])

    result = probe_discord_embed(MEDIA_URL, webhook_url=WEBHOOK_URL, session=session)

    assert result.status == "unknown"
    assert result.embed_pending is False
    assert "very-secret-token" not in (result.error or "")


def test_non_discord_webhook_url_is_rejected_without_request():
    session = FakeSession([])

    result = probe_discord_embed(MEDIA_URL, webhook_url="https://example.com/api/webhooks/1/token", session=session)

    assert result.status == "unknown"
    assert result.error == "invalid Discord webhook URL"
    assert session.calls == []


def test_plain_notice_is_posted_without_mentions():
    session = FakeSession([FakeResponse(200, {"id": "notice-1"})])

    error = post_discord_notice("marked dead\n<https://i.imgur.com/abc.mp4>", webhook_url=WEBHOOK_URL, session=session)

    assert error is None
    assert [call[0] for call in session.calls] == ["POST"]
    assert session.calls[0][2]["json"] == {
        "content": "marked dead\n<https://i.imgur.com/abc.mp4>",
        "allowed_mentions": {"parse": []},
    }
