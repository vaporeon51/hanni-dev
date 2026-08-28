from __future__ import annotations

import struct

import requests

from src.services.media_probe import probe_url


class FakeResponse:
    def __init__(self, status_code: int, *, url: str, content_type: str, body: bytes, content_length: int | None = None):
        self.status_code = status_code
        self.url = url
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size: int = 128):
        yield self._body

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)

    def get(self, url, **kwargs):
        return next(self.responses)


def test_probe_accepts_a_real_image_response():
    response = FakeResponse(
        200,
        url="https://i.imgur.com/abc.png",
        content_type="image/png",
        body=b"\x89PNG\r\n\x1a\nvalid",
    )

    result = probe_url("https://i.imgur.com/abc.png", session=FakeSession([response]))

    assert result.status == "live"
    assert response.closed


def test_probe_rejects_imgur_deleted_placeholder():
    placeholder = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 161, 81) + b"\x00" * 455
    response = FakeResponse(
        200,
        url="https://i.imgur.com/abc.png",
        content_type="image/png",
        body=placeholder,
        content_length=503,
    )

    result = probe_url("https://i.imgur.com/abc.png", session=FakeSession([response]))

    assert result.status == "dead"
    assert result.error == "deleted media placeholder"


def test_probe_keeps_transient_errors_unknown():
    response = FakeResponse(
        503,
        url="https://i.imgur.com/abc.png",
        content_type="text/html",
        body=b"temporarily unavailable",
    )

    result = probe_url("https://i.imgur.com/abc.png", session=FakeSession([response]))

    assert result.status == "unknown"
    assert result.status_code == 503


def test_probe_does_not_fetch_disallowed_hosts():
    class NoRequestSession:
        def get(self, *args, **kwargs):  # pragma: no cover - this should never execute
            raise AssertionError("disallowed host was requested")

    result = probe_url("https://example.com/image.png", session=NoRequestSession())

    assert result.status == "unknown"
    assert "allowlisted" in (result.error or "")
