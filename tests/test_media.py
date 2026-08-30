from __future__ import annotations

import requests

from src.services.media import (
    MediaResolutionError,
    MediaUpstreamError,
    ResolvedMedia,
    open_media_stream,
    resolve_media_url,
)


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


class RateLimitedMetadataResponse:
    status_code = 429
    headers = {"Retry-After": "7"}

    def raise_for_status(self):
        raise requests.HTTPError(response=self)


class RateLimitedMetadataSession:
    def get(self, url, **kwargs):
        return RateLimitedMetadataResponse()


def test_direct_mp4_does_not_need_an_imgur_lookup():
    result = resolve_media_url("https://i.imgur.com/abc123.mp4", client_id="")

    assert result == ResolvedMedia("video", "https://i.imgur.com/abc123.mp4")


def test_direct_goyangi_webp_is_browser_ready():
    url = "https://cdn.goyangi.pics/v1/babymonster/260824-babymonster-ahyeon-asa-5084.webp"

    assert resolve_media_url(url, client_id="") == ResolvedMedia("image", url)


def test_direct_kpopping_jpg_is_browser_ready():
    url = "https://cdn.kpopping.com/kpics/2026/06/1782845536570-2ej1px-2.jpg"

    assert resolve_media_url(url, client_id="") == ResolvedMedia("image", url)


def test_discord_attachment_is_left_as_an_external_link():
    url = "https://cdn.discordapp.com/attachments/1/2/video.mp4?ex=expired"

    assert resolve_media_url(url) == ResolvedMedia("link", url)


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


def test_imgur_rate_limit_is_transient_and_not_resolved_as_a_plain_link():
    try:
        resolve_media_url(
            "https://imgur.com/abc123",
            client_id="client-id",
            session=RateLimitedMetadataSession(),
        )
    except MediaResolutionError as error:
        assert error.retry_after_seconds == 7
    else:  # pragma: no cover
        raise AssertionError("Expected a transient media resolution error")


def test_imgur_page_resolves_static_direct_image():
    session = FakeSession({"data": {"animated": False, "link": "https://i.imgur.com/abc123.jpg"}})

    result = resolve_media_url("https://imgur.com/abc123", client_id="client-id", session=session)

    assert result == ResolvedMedia("image", "https://i.imgur.com/abc123.jpg")


def test_imgur_album_resolves_first_animated_item_to_mp4():
    session = FakeSession(
        {
            "data": [
                {
                    "type": "video/mp4",
                    "animated": True,
                    "link": "https://i.imgur.com/WjBLinK.mp4",
                    "mp4": "https://i.imgur.com/WjBLinK.mp4",
                }
            ]
        }
    )

    result = resolve_media_url(
        "https://imgur.com/a/gF7hDYF",
        client_id="client-id",
        session=session,
    )

    assert result == ResolvedMedia("video", "https://i.imgur.com/WjBLinK.mp4")
    assert session.requests[0][0].endswith("/3/album/gF7hDYF/images")


def test_imgur_album_title_slug_uses_trailing_album_id():
    session = FakeSession(
        {
            "data": [
                {
                    "type": "video/mp4",
                    "animated": True,
                    "link": "https://i.imgur.com/3kcaZ5a.mp4",
                    "mp4": "https://i.imgur.com/3kcaZ5a.mp4",
                }
            ]
        }
    )

    result = resolve_media_url(
        "https://imgur.com/a/karina-x-aespa-capo-ZHB76tL",
        client_id="client-id",
        session=session,
    )

    assert result == ResolvedMedia("video", "https://i.imgur.com/3kcaZ5a.mp4")
    assert session.requests[0][0].endswith("/3/album/ZHB76tL/images")


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


def test_media_stream_allows_goyangi_webp():
    url = "https://cdn.goyangi.pics/v1/babymonster/260824-babymonster-ahyeon-asa-5084.webp"
    session = FakeStreamSession(
        [FakeStreamResponse(status_code=200, content_type="image/webp", url=url)]
    )

    response = open_media_stream(url, session=session)

    assert response.status_code == 200
    assert session.requests[0][0] == url


def test_media_stream_allows_kpopping_jpg():
    url = "https://cdn.kpopping.com/kpics/2026/06/1782845536570-2ej1px-2.jpg"
    session = FakeStreamSession(
        [FakeStreamResponse(status_code=200, content_type="image/jpeg", url=url)]
    )

    response = open_media_stream(url, session=session)

    assert response.status_code == 200
    assert session.requests[0][0] == url


def test_media_stream_rejects_non_imgur_urls_without_requesting():
    session = FakeStreamSession([])

    try:
        open_media_stream("https://example.com/tracker.mp4", session=session)
    except MediaUpstreamError as error:
        assert "allowlisted" in str(error)
    else:  # pragma: no cover
        raise AssertionError("Expected a MediaUpstreamError")

    assert session.requests == []
