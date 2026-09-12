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


class ExhaustedQuotaResponse:
    status_code = 429
    headers = {
        "Retry-After": "5",
        "X-RateLimit-ClientRemaining": "0",
        "X-RateLimit-ClientReset": "3600",
    }

    def raise_for_status(self):
        raise requests.HTTPError(response=self)


class ExhaustedQuotaSession:
    def __init__(self):
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        return ExhaustedQuotaResponse()

    def head(self, url, **kwargs):
        raise requests.ConnectionError("boom")


class FlakySession:
    def __init__(self):
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        raise requests.ConnectionError("boom")

    def head(self, url, **kwargs):
        raise requests.ConnectionError("boom")


def _reset_throttle_state():
    from src.services import media as media_module

    media_module._transient_errors.clear()
    media_module._imgur_cooldown_until = 0.0
    return media_module


def test_exhausted_quota_cools_down_without_recalling(monkeypatch):
    media_module = _reset_throttle_state()
    session = ExhaustedQuotaSession()
    monkeypatch.setattr(media_module, "_shared_session", lambda: session)

    try:
        resolve_media_url("https://imgur.com/abc123", client_id="client-id")
    except MediaResolutionError as error:
        assert error.retry_after_seconds == 5
    else:  # pragma: no cover
        raise AssertionError("Expected a transient media resolution error")
    assert session.requests == [
        "https://api.imgur.com/3/image/abc123",
        "https://imgur.com/abc123",
    ]

    try:
        resolve_media_url("https://imgur.com/abc123", client_id="client-id")
    except MediaResolutionError as error:
        assert error.retry_after_seconds == 5
    else:  # pragma: no cover
        raise AssertionError("Expected the cached transient error without refetching")
    assert session.requests == [
        "https://api.imgur.com/3/image/abc123",
        "https://imgur.com/abc123",
    ]

    import time

    expired, error = media_module._transient_errors["https://imgur.com/abc123"]
    media_module._transient_errors["https://imgur.com/abc123"] = (time.monotonic() - 1, error)
    try:
        resolve_media_url("https://imgur.com/abc123", client_id="client-id")
    except MediaResolutionError as error:
        assert 3300 < error.retry_after_seconds <= 3600
    else:  # pragma: no cover
        raise AssertionError("Expected a cooldown error after cache expiry")
    assert session.requests == [
        "https://api.imgur.com/3/image/abc123",
        "https://imgur.com/abc123",
        "https://imgur.com/abc123",
    ]
    _reset_throttle_state()


def test_transient_network_failure_is_cached_briefly(monkeypatch):
    import time

    media_module = _reset_throttle_state()
    session = FlakySession()
    monkeypatch.setattr(media_module, "_shared_session", lambda: session)

    for _ in range(2):
        try:
            resolve_media_url("https://imgur.com/abc123", client_id="client-id")
        except MediaResolutionError:
            pass
        else:  # pragma: no cover
            raise AssertionError("Expected a transient media resolution error")
    assert len(session.requests) == 2

    expired, error = media_module._transient_errors["https://imgur.com/abc123"]
    media_module._transient_errors["https://imgur.com/abc123"] = (time.monotonic() - 1, error)
    try:
        resolve_media_url("https://imgur.com/abc123", client_id="client-id")
    except MediaResolutionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("Expected a fresh lookup after cache expiry")
    assert len(session.requests) == 4
    _reset_throttle_state()


def test_explicit_session_bypasses_process_throttle(monkeypatch):
    media_module = _reset_throttle_state()
    media_module._imgur_cooldown_until = 9999999999.0

    session = FakeSession({"data": {"animated": False, "link": "https://i.imgur.com/abc123.jpg"}})
    result = resolve_media_url("https://imgur.com/abc123", client_id="client-id", session=session)

    assert result == ResolvedMedia("image", "https://i.imgur.com/abc123.jpg")
    _reset_throttle_state()


class ProbeHeadResponse:
    def __init__(self, status_code=200, content_type="video/mp4"):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        if self.status_code != 200:
            raise requests.HTTPError(response=self)


class ProbeHeadSession:
    def __init__(self, mapping):
        self.mapping = mapping
        self.head_requests = []
        self.get_requests = []

    def head(self, url, **kwargs):
        self.head_requests.append(url)
        status, content_type = self.mapping.get(url, (404, "text/html"))
        return ProbeHeadResponse(status, content_type)

    def get(self, url, **kwargs):
        self.get_requests.append(url)
        return ProbeHeadResponse(404, "text/html")


def test_probe_resolves_video_without_api_call(monkeypatch):
    media_module = _reset_throttle_state()
    session = ProbeHeadSession({"https://i.imgur.com/abc123.mp4": (200, "video/mp4")})
    monkeypatch.setattr(media_module, "_shared_session", lambda: session)

    result = resolve_media_url("https://imgur.com/abc123", client_id="client-id")

    assert result == ResolvedMedia("video", "https://i.imgur.com/abc123.mp4")
    assert session.head_requests == ["https://i.imgur.com/abc123.mp4"]
    assert session.get_requests == []
    _reset_throttle_state()


def test_probe_falls_back_to_jpg_image(monkeypatch):
    media_module = _reset_throttle_state()
    session = ProbeHeadSession({"https://i.imgur.com/abc123.jpg": (200, "image/jpeg")})
    monkeypatch.setattr(media_module, "_shared_session", lambda: session)

    result = resolve_media_url("https://imgur.com/abc123", client_id="client-id")

    assert result == ResolvedMedia("image", "https://i.imgur.com/abc123.jpg")
    assert session.head_requests == [
        "https://i.imgur.com/abc123.mp4",
        "https://i.imgur.com/abc123.jpg",
    ]
    _reset_throttle_state()


def test_probe_miss_falls_back_to_api_then_link(monkeypatch):
    media_module = _reset_throttle_state()
    session = ProbeHeadSession({})
    monkeypatch.setattr(media_module, "_shared_session", lambda: session)

    result = resolve_media_url("https://imgur.com/abc123", client_id="client-id")

    assert result == ResolvedMedia("link", "https://imgur.com/abc123")
    assert len(session.head_requests) == 4
    assert len(session.get_requests) == 1
    _reset_throttle_state()


def test_probe_skipped_for_albums(monkeypatch):
    media_module = _reset_throttle_state()
    session = ProbeHeadSession({})
    monkeypatch.setattr(media_module, "_shared_session", lambda: session)

    result = resolve_media_url("https://imgur.com/a/XYZ123", client_id="client-id")

    assert result == ResolvedMedia("link", "https://imgur.com/a/XYZ123")
    assert session.head_requests == []
    _reset_throttle_state()


class GoyangiPageResponse:
    def __init__(self, url, text=""):
        self.url = url
        self.text = text


class GoyangiPageSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        if self.error is not None:
            raise self.error
        return self.response


def test_goyangi_viewer_page_follows_meta_refresh():
    session = GoyangiPageSession(
        GoyangiPageResponse(
            "https://goyangi.pics/v/260911-stayc-j-2014f9db.mp4",
            '<meta http-equiv="refresh" content="0; url=https://cdn.goyangi.pics/v1/stayc/j/260911-stayc-j-f9db.mp4">',
        )
    )

    result = resolve_media_url("https://goyangi.pics/v/260911-stayc-j-2014f9db.mp4", session=session)

    assert result == ResolvedMedia(
        "video", "https://cdn.goyangi.pics/v1/stayc/j/260911-stayc-j-f9db.mp4"
    )


def test_goyangi_http_redirect_target_is_used_directly():
    session = GoyangiPageSession(
        GoyangiPageResponse("https://cdn.goyangi.pics/v/260912-stayc-isa-cbbf8538.webp")
    )

    result = resolve_media_url("https://goyangi.pics/v/260912-stayc-isa-cbbf8538.webp", session=session)

    assert result == ResolvedMedia(
        "image", "https://cdn.goyangi.pics/v/260912-stayc-isa-cbbf8538.webp"
    )


def test_goyangi_unresolvable_page_stays_a_plain_link():
    page_url = "https://goyangi.pics/v/260911-stayc-j-2014f9db.mp4"

    failing = GoyangiPageSession(error=requests.ConnectionError("boom"))
    assert resolve_media_url(page_url, session=failing) == ResolvedMedia("link", page_url)

    off_allowlist = GoyangiPageSession(
        GoyangiPageResponse(page_url, '<meta http-equiv="refresh" content="0; url=https://evil.example/x.mp4">')
    )
    assert resolve_media_url(page_url, session=off_allowlist) == ResolvedMedia("link", page_url)

    assert failing.requests == [page_url]


OG_ALBUM_HTML = (
    "<html><head>"
    '<meta property="og:video" content="https://i.imgur.com/ABC123.mp4" />'
    '<meta property="og:image" content="https://i.imgur.com/ABC123.jpg" />'
    '<meta property="og:title" content="aespa" />'
    "</head></html>"
)


class OgPageResponse:
    status_code = 200

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class OgFallbackSession:
    """Probes miss, the API is quota-dead, page HTML carries unfurl tags."""

    def __init__(self, html=OG_ALBUM_HTML):
        self.html = html
        self.head_requests = []
        self.get_requests = []

    def head(self, url, **kwargs):
        self.head_requests.append(url)
        return ProbeHeadResponse(404, "text/html")

    def get(self, url, **kwargs):
        self.get_requests.append(url)
        if "/3/album/" in url or "/3/image/" in url:
            return ExhaustedQuotaResponse()
        return OgPageResponse(self.html)


def _api_calls(session):
    return [url for url in session.get_requests if "/3/album/" in url or "/3/image/" in url]


def test_og_fallback_rescues_album_on_dead_quota(monkeypatch):
    media_module = _reset_throttle_state()
    session = OgFallbackSession()
    monkeypatch.setattr(media_module, "_shared_session", lambda: session)

    result = resolve_media_url("https://imgur.com/a/XYZ123", client_id="client-id")

    assert result == ResolvedMedia("video", "https://i.imgur.com/ABC123.mp4")
    assert session.head_requests == []
    assert len(_api_calls(session)) == 1
    _reset_throttle_state()


def test_og_image_only_yields_image(monkeypatch):
    media_module = _reset_throttle_state()
    session = OgFallbackSession(
        '<html><head><meta property="og:image" content="https://i.imgur.com/ABC123.jpg" /></head></html>'
    )
    monkeypatch.setattr(media_module, "_shared_session", lambda: session)

    result = resolve_media_url("https://imgur.com/a/XYZ123", client_id="client-id")

    assert result == ResolvedMedia("image", "https://i.imgur.com/ABC123.jpg")
    _reset_throttle_state()


def test_og_missing_tags_preserves_transient_error(monkeypatch):
    media_module = _reset_throttle_state()
    session = OgFallbackSession("<html><head></head></html>")
    monkeypatch.setattr(media_module, "_shared_session", lambda: session)

    for _ in range(2):
        try:
            resolve_media_url("https://imgur.com/a/XYZ123", client_id="client-id")
        except MediaResolutionError:
            pass
        else:  # pragma: no cover
            raise AssertionError("Expected a transient media resolution error")

    assert len(_api_calls(session)) == 1
    assert len(session.get_requests) == 2
    _reset_throttle_state()
