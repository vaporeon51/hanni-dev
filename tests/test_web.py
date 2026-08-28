from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from src.db.feedback import ContentFeedback
from src.db.feed import FeedItem
from src.services.media import ResolvedMedia
from src.web import app as web_app


def test_feed_endpoint_serializes_feed_items(monkeypatch):
    async def fake_load_feed(**kwargs):
        assert kwargs["sort"] == "latest"
        return [
            FeedItem(
                content_link_id=42,
                role_id="role-1",
                member_name="Hanni",
                group_name="NewJeans",
                url="https://i.imgur.com/example.png",
                original_url=None,
                uploaded_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                score=3.0,
            )
        ]

    monkeypatch.setattr(web_app, "load_feed", fake_load_feed)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/feed?sort=latest&limit=1")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json()["items"][0]["label"] == "Hanni - NewJeans"
    assert response.json()["items"][0]["uploaded_date"] == "2026-01-01T00:00:00+00:00"


def test_feed_endpoint_rejects_unknown_sort():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/feed?sort=bias")

    response = asyncio.run(request())

    assert response.status_code == 400


def test_feed_endpoint_caps_items_at_thirty():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/feed?limit=31")

    response = asyncio.run(request())

    assert response.status_code == 422


def test_feed_endpoint_rate_limits_repeated_searches(monkeypatch):
    async def fake_load_feed(**kwargs):
        assert kwargs["limit"] == 15
        return []

    monkeypatch.setattr(web_app, "load_feed", fake_load_feed)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get("/api/feed")
            second = await client.get("/api/feed")
            return first, second

    first, second = asyncio.run(request())

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"] == "10"


def test_vote_endpoint_returns_updated_feedback(monkeypatch):
    def fake_add_vote(content_link_id, direction):
        assert content_link_id == 4201
        assert direction == "up"
        return ContentFeedback(upvotes=4, downvotes=1, reports=0)

    monkeypatch.setattr(web_app, "add_content_vote", fake_add_vote)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/feed/4201/vote/up")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json() == {
        "upvotes": 4,
        "downvotes": 1,
        "reports": 0,
        "vote_score": 3,
    }


def test_wrong_idol_report_endpoint_returns_updated_feedback(monkeypatch):
    def fake_add_report(content_link_id, reason):
        assert content_link_id == 4202
        assert reason == "wrong_idol"
        return ContentFeedback(upvotes=2, downvotes=0, reports=1)

    monkeypatch.setattr(web_app, "add_content_report", fake_add_report)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/feed/4202/report?reason=wrong_idol")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json()["reports"] == 1
    assert "dead_link_reports" not in response.json()


def test_dead_link_report_endpoint_returns_threshold_state(monkeypatch):
    def fake_add_report(content_link_id, reason):
        assert content_link_id == 4203
        assert reason == "dead_link"
        return ContentFeedback(
            upvotes=1,
            downvotes=0,
            reports=0,
            dead_link_reports=3,
            is_dead=True,
        )

    monkeypatch.setattr(web_app, "add_content_report", fake_add_report)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/feed/4203/report?reason=dead_link")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert "dead_link_reports" not in response.json()
    assert "is_dead" not in response.json()


def test_report_endpoint_requires_known_reason():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.post("/api/feed/4204/report")
            unknown = await client.post("/api/feed/4204/report?reason=spam")
            return missing, unknown

    missing, unknown = asyncio.run(request())

    assert missing.status_code == 422
    assert unknown.status_code == 422


def test_media_endpoint_returns_one_resolved_asset(monkeypatch):
    queued = []
    monkeypatch.setattr(web_app, "get_live_content_url", lambda content_link_id: "https://imgur.com/abc123")
    monkeypatch.setattr(web_app, "enqueue_priority_url", queued.append)
    monkeypatch.setattr(
        web_app,
        "resolve_media_url_cached",
        lambda url: ResolvedMedia("video", "https://i.imgur.com/abc123.mp4"),
    )

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/feed/42/media")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json() == {"kind": "video", "url": "/api/feed/42/asset"}
    assert queued == ["https://imgur.com/abc123"]


def test_media_asset_proxies_range_response(monkeypatch):
    class FakeUpstream:
        status_code = 206
        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": "4",
            "Content-Range": "bytes 0-3/20",
            "Accept-Ranges": "bytes",
        }

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"test"

        def close(self):
            return None

    monkeypatch.setattr(web_app, "get_live_content_url", lambda content_link_id: "https://i.imgur.com/abc123.mp4")
    monkeypatch.setattr(
        web_app,
        "resolve_media_url_cached",
        lambda url: ResolvedMedia("video", "https://i.imgur.com/abc123.mp4"),
    )

    def fake_open_media_stream(url, range_header):
        assert url == "https://i.imgur.com/abc123.mp4"
        assert range_header == "bytes=0-3"
        return FakeUpstream()

    monkeypatch.setattr(web_app, "open_media_stream", fake_open_media_stream)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/feed/42/asset", headers={"Range": "bytes=0-3"})

    response = asyncio.run(request())

    assert response.status_code == 206
    assert response.content == b"test"
    assert response.headers["content-range"] == "bytes 0-3/20"


def test_homepage_renders():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert "hanni" in response.text
    assert '<a class="brand-link" href="/" aria-label="hanni home">' in response.text
    assert "search a member or group" in response.text
    assert '<footer class="site-credit">made by glaceon</footer>' in response.text
    assert '/static/app.css?v=' in response.text
    assert '/static/app.js?v=' in response.text
    assert "fonts.googleapis.com" not in response.text
    css = (web_app.REPO_ROOT / "static" / "app.css").read_text()
    assert 'font-family: Georgia, "Times New Roman", serif;' in css
    assert "font-weight: 400;" in css
    assert ".card-media video" in css
    assert ".card-media img, .card-media video" in css
    assert "width: auto" in css
    assert "max-width: min(100%, 520px)" in css
    assert "width: min(560px, 100%)" in css
    assert "max-height: min(62vh, 540px)" in css
    assert '<option value="15" selected>15 links</option>' in response.text
    assert '<option value="random" selected>random</option>' in response.text
    assert '<option value="latest">latest</option>' in response.text
    assert "Loading little links" not in response.text
    assert "a tiny corner for good links" not in response.text
    assert "autofeed" not in response.text.lower()
    assert response.text.index('id="feed"') < response.text.index('class="feed-status"')


def test_client_waits_for_search_and_autoplays_video():
    script = (web_app.REPO_ROOT / "static" / "app.js").read_text()

    assert not script.rstrip().endswith("loadFeed();")
    assert "new Set([1, 15, 30])" in script
    assert 'new Set(["random", "latest", "oldest", "top"])' in script
    assert "new URLSearchParams({ limit: String(limit), sort })" in script
    assert "media.autoplay = true" in script
    assert "videoPlaybackObserver" in script
    assert "REVEAL_DELAY_MS = 2000" in script
    assert "scrollIntoView" in script
    assert 'block: "end"' in script
    assert '$("query").blur()' in script
    assert '$("stop-feed").addEventListener("click", stopFeed)' in script
    assert '$("skip-latest").addEventListener("click", skipToLatest)' in script
    assert '$("move-top").addEventListener("click", moveToTop)' in script
    assert 'search.scrollIntoView' in script
    assert 'query").focus({ preventScroll: true })' in script
    assert 'feedbackButton("report", "report", "report", undefined, "Report wrong idol")' in script
    assert 'const reportReason = action === "report" ? "wrong_idol" : "";' in script
    assert 'select[data-action="report"]' not in script
    assert '["dead_link", "dead link"]' not in script
    assert "dead link report ${payload.dead_link_reports} of 3" not in script
    assert "state.visibleCount > 1" not in script
    assert 'removeAttribute("src")' not in script
    assert "dataset.mediaSrc" not in script
    assert "mediaCandidates" not in script
    assert "if (copied) await recordImplicitUpvote(card, id);" in script
    assert "async function recordImplicitUpvote(card, id)" in script
