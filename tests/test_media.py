from __future__ import annotations

from src.services.media import MediaUpstreamError, ResolvedMedia, open_media_stream, resolve_media_url


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return FakeResponse(self.payload)


def test_direct_mp4_does_not_need_an_imgur_lookup():
    result = resolve_media_url("https://i.imgur.com/abc123.mp4", client_id="")

    assert result == ResolvedMedia("video", "https://i.imgur.com/abc123.mp4")


def test_imgur_page_resolves_to_animated_mp4():
    session = FakeSession(
        {
            "data": {
                "animated": True,
                "link": "https://i.imgur.com/abc123.gif",
                "mp4": "https://i.imgur.com/abc123.mp4",
            }
        }
    )

    result = resolve_media_url("https://imgur.com/abc123", client_id="client-id", session=session)

    assert result == ResolvedMedia("video", "https://i.imgur.com/abc123.mp4")
    assert session.requests[0][0].endswith("/3/image/abc123")


def test_imgur_page_resolves_static_direct_image():
    session = FakeSession({"data": {"animated": False, "link": "https://i.imgur.com/abc123.jpg"}})

    result = resolve_media_url("https://imgur.com/abc123", client_id="client-id", session=session)

    assert result == ResolvedMedia("image", "https://i.imgur.com/abc123.jpg")


def test_imgur_metadata_cannot_redirect_media_to_another_host():
    session = FakeSession({"data": {"mp4": "https://example.com/tracker.mp4"}})

    result = resolve_media_url("https://imgur.com/abc123", client_id="client-id", session=session)

    assert result == ResolvedMedia("link", "https://imgur.com/abc123")


class FakeStreamResponse:
    def __init__(self, status_code=206, content_type="video/mp4", url="https://i.imgur.com/abc123.mp4"):
        self.status_code = status_code
        self.url = url
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def close(self):
        self.closed = True


class FakeStreamSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return next(self.responses)


def test_media_stream_forwards_a_valid_range_header():
    session = FakeStreamSession([FakeStreamResponse()])

    response = open_media_stream(
        "https://i.imgur.com/abc123.mp4",
        range_header="bytes=0-1023",
        session=session,
    )

    assert response.status_code == 206
    assert session.requests[0][1]["headers"]["Range"] == "bytes=0-1023"


def test_media_stream_rejects_non_imgur_urls_without_requesting():
    session = FakeStreamSession([])

    try:
        open_media_stream("https://example.com/tracker.mp4", session=session)
    except MediaUpstreamError as error:
        assert "allowlisted" in str(error)
    else:  # pragma: no cover
        raise AssertionError("Expected a MediaUpstreamError")

    assert session.requests == []
