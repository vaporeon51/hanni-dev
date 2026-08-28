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
    assert response.json()["items"][0]["label"] == "Hanni — NewJeans"
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
    assert response.json() == {"upvotes": 4, "downvotes": 1, "reports": 0, "vote_score": 3}


def test_report_endpoint_returns_updated_feedback(monkeypatch):
    def fake_add_report(content_link_id):
        assert content_link_id == 4202
        return ContentFeedback(upvotes=2, downvotes=0, reports=1)

    monkeypatch.setattr(web_app, "add_content_report", fake_add_report)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/feed/4202/report")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json()["reports"] == 1


def test_media_endpoint_returns_one_resolved_asset(monkeypatch):
    monkeypatch.setattr(web_app, "get_live_content_url", lambda content_link_id: "https://imgur.com/abc123")
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
    assert response.json() == {"kind": "video", "url": "https://i.imgur.com/abc123.mp4"}


def test_homepage_renders():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert "hanni" in response.text
    assert "search a member or group" in response.text
    assert '<option value="15" selected>15 links</option>' in response.text
    assert "Loading little links" not in response.text
    assert "a tiny corner for good links" not in response.text
    assert "autofeed" not in response.text.lower()


def test_client_waits_for_search_and_autoplays_video():
    script = (web_app.REPO_ROOT / "static" / "app.js").read_text()

    assert not script.rstrip().endswith("loadFeed();")
    assert "new Set([1, 15, 30])" in script
    assert "media.autoplay = true" in script
    assert "videoPlaybackObserver" in script
    assert "REVEAL_DELAY_MS = 2000" in script
    assert "scrollIntoView" in script
    assert "mediaCandidates" not in script
