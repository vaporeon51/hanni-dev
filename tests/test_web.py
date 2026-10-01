from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from src.db.feedback import ContentFeedback
from src.db.feed import FeedItem
from src.db.collections import ContentCollection, ContentSet, CollectionPreview
from src.services.media import MediaResolutionError, ResolvedMedia
from src.web import app as web_app


def sample_item(*, content_link_id: int, url: str) -> FeedItem:
    return FeedItem(
        content_link_id=content_link_id,
        role_id="role-1",
        member_name="Karina",
        group_name="aespa",
        url=url,
        original_url=None,
        uploaded_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        score=1.0,
    )


def test_feed_endpoint_serializes_feed_items(monkeypatch):
    async def fake_load_feed(**kwargs):
        assert kwargs["sort"] == "latest"
        assert kwargs["query"] == "Hanni - NewJeans"
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
            return await client.get("/api/feed?q=Hanni+-+NewJeans&sort=latest&limit=1")

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
        assert kwargs["limit"] == 5
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
    assert second.headers["retry-after"] == "3"


def test_random_feed_uses_the_visitors_recent_media_history(monkeypatch):
    calls = []

    async def fake_load_feed(**kwargs):
        calls.append(kwargs)
        assert kwargs["recent_urls"] == ("https://i.imgur.com/recent.mp4",)
        assert kwargs["exclude_recent"] is True
        return [
            FeedItem(
                content_link_id=44,
                role_id="role-1",
                member_name="Hanni",
                group_name="NewJeans",
                url="https://i.imgur.com/new.mp4",
                original_url=None,
                uploaded_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                score=1.0,
            )
        ]

    monkeypatch.setattr(web_app, "load_feed", fake_load_feed)
    monkeypatch.setattr(
        web_app.feed_history,
        "recent_urls",
        lambda visitor_id: ("https://i.imgur.com/recent.mp4",),
    )

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={web_app.VISITOR_COOKIE: "feed-history-test"},
        ) as client:
            return await client.get("/api/feed?sort=random")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert len(calls) == 1


def test_top_feed_continuation_uses_a_stable_offset(monkeypatch):
    calls = []

    async def fake_load_feed(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(web_app, "load_feed", fake_load_feed)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={web_app.VISITOR_COOKIE: "top-feed-pagination-test"},
        ) as client:
            return await client.get("/api/feed?sort=top&limit=5&continuation=true&offset=10")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert calls == [
        {
            "query": None,
            "sort": "top",
            "limit": 5,
            "recent_urls": (),
            "exclude_recent": False,
            "offset": 10,
        }
    ]


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
    remembered = []

    async def fake_preview(content_link_id):
        assert content_link_id == 42
        return CollectionPreview(url="https://imgur.com/abc123", count=5)

    monkeypatch.setattr(web_app, "load_collection_preview", fake_preview)
    monkeypatch.setattr(web_app, "enqueue_priority_url", queued.append)
    monkeypatch.setattr(web_app.feed_history, "remember", lambda visitor_id, url: remembered.append((visitor_id, url)))
    monkeypatch.setattr(
        web_app,
        "resolve_media_url_cached",
        lambda url: ResolvedMedia("video", "https://i.imgur.com/abc123.mp4"),
    )

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={web_app.VISITOR_COOKIE: "media-history-test"},
        ) as client:
            return await client.get("/api/feed/42/media")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json() == {"kind": "video", "url": "/api/feed/42/asset", "collection_count": 5}
    assert queued == ["https://imgur.com/abc123"]
    assert remembered == [("media-history-test", "https://imgur.com/abc123")]


def test_media_endpoint_exposes_transient_resolution_backoff(monkeypatch):
    async def fake_preview(content_link_id):
        return CollectionPreview(url="https://imgur.com/abc123", count=1)

    def fail_temporarily(url):
        raise MediaResolutionError("rate limited", retry_after_seconds=7)

    monkeypatch.setattr(web_app, "load_collection_preview", fake_preview)
    monkeypatch.setattr(web_app, "enqueue_priority_url", lambda url: None)
    monkeypatch.setattr(web_app, "resolve_media_url_cached", fail_temporarily)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/feed/42/media")

    response = asyncio.run(request())

    assert response.status_code == 503
    assert response.headers["retry-after"] == "7"


def test_collection_endpoint_reuses_serialized_feed_items(monkeypatch):
    async def fake_collection(content_link_id):
        assert content_link_id == 42
        return ContentCollection(
            label="Hanni - NewJeans",
            items=(
                FeedItem(
                    content_link_id=42,
                    role_id="role-1",
                    member_name="Hanni",
                    group_name="NewJeans",
                    url="https://i.imgur.com/one.mp4",
                    original_url=None,
                    uploaded_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    score=3.0,
                ),
                FeedItem(
                    content_link_id=43,
                    role_id="role-1",
                    member_name="Hanni",
                    group_name="NewJeans",
                    url="https://i.imgur.com/two.mp4",
                    original_url=None,
                    uploaded_date=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
                    score=2.0,
                ),
            ),
        )

    monkeypatch.setattr(web_app, "load_collection", fake_collection)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/collections/42")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json()["label"] == "Hanni - NewJeans"
    assert response.json()["count"] == 2
    assert [item["content_link_id"] for item in response.json()["items"]] == [42, 43]


def test_set_feed_endpoint_serializes_whole_sets(monkeypatch):
    async def fake_set_feed(**kwargs):
        assert kwargs == {
            "query": "hanni",
            "sort": "latest",
            "limit": 2,
            "cursor_date": None,
            "cursor_id": None,
        }
        return [
            ContentSet(
                collection_of=42,
                label="Hanni - NewJeans",
                items=(
                    FeedItem(
                        content_link_id=42,
                        role_id="role-1",
                        member_name="Hanni",
                        group_name="NewJeans",
                        url="https://i.imgur.com/one.mp4",
                        original_url=None,
                        uploaded_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        score=3.0,
                    ),
                    FeedItem(
                        content_link_id=43,
                        role_id="role-1",
                        member_name="Hanni",
                        group_name="NewJeans",
                        url="https://i.imgur.com/two.mp4",
                        original_url=None,
                        uploaded_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        score=2.0,
                    ),
                ),
                set_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        ]

    monkeypatch.setattr(web_app, "load_collection_feed", fake_set_feed)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={web_app.VISITOR_COOKIE: "set-feed-endpoint-test"},
        ) as client:
            return await client.get("/api/sets?q=hanni&sort=latest&limit=1")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["sets"][0]["collection_of"] == 42
    assert len(response.json()["sets"][0]["items"]) == 2
    assert response.json()["next_cursor"] is None


def test_set_feed_endpoint_returns_a_stable_next_cursor(monkeypatch):
    first_date = datetime(2026, 1, 2, tzinfo=timezone.utc)

    async def fake_set_feed(**kwargs):
        assert kwargs["limit"] == 2
        return [
            ContentSet(collection_of=42, label="First", items=(), set_date=first_date),
            ContentSet(
                collection_of=41,
                label="Second",
                items=(),
                set_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]

    monkeypatch.setattr(web_app, "load_collection_feed", fake_set_feed)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={web_app.VISITOR_COOKIE: "set-pagination-test"},
        ) as client:
            return await client.get("/api/sets?sort=latest&limit=1")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["next_cursor"] == f"{first_date.isoformat()}|42"


def test_set_cursor_round_trips_timezone_less_database_dates():
    database_date = datetime(2026, 1, 2, 3, 4, 5)

    cursor = web_app._encode_set_cursor(database_date, 42)

    assert cursor == "2026-01-02T03:04:05+00:00|42"
    assert web_app._decode_set_cursor(cursor) == (database_date, 42)


def test_scroll_endpoint_returns_and_reserves_a_random_batch(monkeypatch):
    remembered = []

    async def fake_load_feed(**kwargs):
        assert kwargs == {
            "query": "hanni",
            "sort": "random",
            "limit": 8,
            "recent_urls": ("https://i.imgur.com/recent.mp4",),
            "exclude_recent": True,
        }
        return [
            FeedItem(
                content_link_id=82,
                role_id="role-1",
                member_name="Hanni",
                group_name="NewJeans",
                url="https://i.imgur.com/scroll.mp4",
                original_url=None,
                uploaded_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                score=3.0,
            )
        ]

    monkeypatch.setattr(web_app, "load_feed", fake_load_feed)
    monkeypatch.setattr(
        web_app.scroll_history,
        "recent_urls",
        lambda visitor_id: ("https://i.imgur.com/recent.mp4",),
    )
    monkeypatch.setattr(
        web_app.scroll_history,
        "remember",
        lambda visitor_id, url: remembered.append((visitor_id, url)),
    )

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={web_app.VISITOR_COOKIE: "scroll-feed-endpoint-test"},
        ) as client:
            return await client.get("/api/scroll?q=hanni&limit=8")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json()["items"][0]["content_link_id"] == 82
    assert response.json()["cycle_reset"] is False
    assert remembered == [("scroll-feed-endpoint-test", "https://i.imgur.com/scroll.mp4")]


def test_analytics_endpoint_records_only_the_first_request_in_a_session(monkeypatch):
    recorded = []
    monkeypatch.setattr(web_app, "record_country_session", recorded.append)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={web_app.VISITOR_COOKIE: "analytics-session-test"},
        ) as client:
            first = await client.post("/api/analytics/session", json={"country_code": "us"})
            second = await client.post("/api/analytics/session", json={"country_code": "CA"})
            return first, second

    first, second = asyncio.run(request())

    assert first.status_code == 200
    assert first.json() == {"recorded": True}
    assert second.json() == {"recorded": False}
    assert recorded == ["US"]
    assert web_app.ANALYTICS_SESSION_COOKIE in first.cookies


def test_analytics_country_code_falls_back_to_unknown():
    assert web_app._country_code(" ca ") == "CA"
    assert web_app._country_code("not-a-country") == "XX"


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


def test_homepage_renders_menu_with_wholesome_and_nsfw():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert "<h1>hanni" in response.text
    assert "wholesome</h2>" in response.text
    assert "nsfw</h2>" in response.text
    for path in ("/sorter", "/leaderboard", "/feed", "/sets", "/scroll"):
        assert f'<a class="menu-card" href="{path}">' in response.text
    assert '<footer class="site-credit">made by glaceon' in response.text
    assert '/disambiguation' not in response.text
    assert '/static/home.css?v=' in response.text
    # Emoji are banned from chrome; iconography goes through icons.svg.
    for banned in ("😇", "😈", "💘", "🏆", "🎲", "🗂", "🌀"):
        for template in ("home.html", "_nav.html", "sorter.html", "leaderboard.html"):
            assert banned not in (web_app.REPO_ROOT / "templates" / template).read_text()
    assert (web_app.REPO_ROOT / "static" / "icons.svg").exists()
    assert 'use href="/static/icons.svg' in response.text


def test_nsfw_pages_use_the_dark_theme():
    async def request(path):
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    for path in ("/feed", "/sets", "/scroll"):
        response = asyncio.run(request(path))
        assert response.status_code == 200
        assert "/static/nsfw.css?v=" in response.text
    assert 'class="nsfw"' in asyncio.run(request("/feed")).text
    assert 'class="scroll-page nsfw"' in asyncio.run(request("/scroll")).text
    css = (web_app.REPO_ROOT / "static" / "nsfw.css").read_text()
    assert "body.nsfw" in css
    assert "--page: #161219" in css


def test_wholesome_pages_stay_light():
    async def request(path):
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    for path in ("/", "/sorter", "/leaderboard"):
        response = asyncio.run(request(path))
        assert response.status_code == 200
        assert "/static/nsfw.css" not in response.text


def test_feed_page_renders():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/feed")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert "search an idol or group" in response.text
    assert '<a href="/feed" aria-current="page">feed</a>' in response.text
    assert "fonts.googleapis.com" not in response.text
    assert 'id="collection-heading"' in response.text
    assert '/static/app.js?v=' in response.text
    assert 'id="feed-sentinel"' in response.text


def test_sets_page_renders_separately():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/sets")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert "search sets by idol, group, or link" in response.text
    assert '/static/sets.js?v=' in response.text
    assert '<option value="latest" selected>newest</option>' in response.text
    assert '<option value="oldest">oldest</option>' in response.text
    assert 'id="feed-sentinel"' in response.text


def test_scroll_page_renders_as_a_separate_reel_surface():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/scroll")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert 'id="reel-feed"' in response.text
    assert 'id="scroll-form"' in response.text
    assert '/static/scroll.css?v=' in response.text
    assert '/static/scroll.js?v=' in response.text
    assert 'placeholder="search an idol or group"' in response.text
    assert 'id="scroll-hint"' in response.text
    assert "scroll for more" in response.text


def test_plain_link_endpoint_avoids_recent_urls_and_returns_source(monkeypatch):
    calls = []
    analytics = []

    async def fake_load_feed(**kwargs):
        calls.append(kwargs)
        return [sample_item(content_link_id=len(calls), url=f"https://i.imgur.com/{len(calls)}.mp4")]

    monkeypatch.setattr(web_app, "load_feed", fake_load_feed)
    monkeypatch.setattr(web_app, "record_link_request", lambda **kwargs: analytics.append(kwargs))
    web_app.link_history.clear("aespa")

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get("/api/link?q=Aespa")
            second = await client.get("/api/link?q=aespa")
            return first, second

    first, second = asyncio.run(request())

    assert first.status_code == 200
    assert first.text == "https://i.imgur.com/1.mp4"
    assert first.headers["content-type"].startswith("text/plain")
    assert first.headers["cache-control"] == "no-store"
    assert second.text == "https://i.imgur.com/2.mp4"
    assert calls[0]["query"] == "Aespa"
    assert calls[0]["limit"] == 1
    assert calls[0]["recent_urls"] == ()
    assert calls[1]["recent_urls"] == ("https://i.imgur.com/1.mp4",)
    assert analytics == [
        {"found": True, "cycle_reset": False},
        {"found": True, "cycle_reset": False},
    ]


def test_plain_link_endpoint_restarts_an_exhausted_small_pool(monkeypatch):
    responses = [[], [sample_item(content_link_id=9, url="https://i.imgur.com/repeat.mp4")]]
    analytics = []

    async def fake_load_feed(**_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(web_app, "load_feed", fake_load_feed)
    monkeypatch.setattr(web_app, "record_link_request", lambda **kwargs: analytics.append(kwargs))
    web_app.link_history.clear("tiny")
    web_app.link_history.remember("tiny", "https://i.imgur.com/repeat.mp4")

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/link?q=tiny")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.text == "https://i.imgur.com/repeat.mp4"
    assert analytics == [{"found": True, "cycle_reset": True}]


def test_plain_link_endpoint_records_no_result_without_query_text(monkeypatch):
    analytics = []

    async def fake_load_feed(**_kwargs):
        return []

    monkeypatch.setattr(web_app, "load_feed", fake_load_feed)
    monkeypatch.setattr(web_app, "record_link_request", lambda **kwargs: analytics.append(kwargs))
    web_app.link_history.clear("missing")

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/link?q=missing")

    response = asyncio.run(request())

    assert response.status_code == 404
    assert analytics == [{"found": False, "cycle_reset": False}]


def test_collections_by_url_returns_matching_sets(monkeypatch):
    from src.db.collections import ContentSet

    async def fake_load_collections_for_url(url):
        assert url == "https://i.imgur.com/tNe8t7L.mp4"
        return [
            ContentSet(
                collection_of=7,
                label="Karina - aespa",
                items=[sample_item(content_link_id=7, url="https://i.imgur.com/tNe8t7L.mp4")],
            )
        ]

    monkeypatch.setattr(web_app, "load_collections_for_url", fake_load_collections_for_url)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/collections/by-url", params={"url": "https://i.imgur.com/tNe8t7L.mp4"})

    response = asyncio.run(request())

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["sets"][0]["collection_of"] == 7
    assert payload["sets"][0]["label"] == "Karina - aespa"
    assert payload["sets"][0]["items"][0]["url"] == "https://i.imgur.com/tNe8t7L.mp4"


def test_collections_by_url_rejects_blank_url():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/collections/by-url", params={"url": "   "})

    response = asyncio.run(request())

    assert response.status_code == 400


def test_temporarily_missing_media_returns_uncached_404_without_db_write(monkeypatch):
    from src.services.media import MediaUnavailableError
    from src.db import POOL

    async def preview(_):
        return CollectionPreview(url="https://goyangi.pics/v/missing.webp", count=1)

    def unavailable(_):
        raise MediaUnavailableError("upstream 404")

    def unexpected_db_access():
        raise AssertionError("Temporary media failures must not mutate database state")

    monkeypatch.setattr(web_app, "load_collection_preview", preview)
    monkeypatch.setattr(web_app, "get_live_content_url", lambda _: "https://goyangi.pics/v/missing.webp")
    monkeypatch.setattr(web_app, "resolve_media_url_cached", unavailable)
    monkeypatch.setattr(web_app, "enqueue_priority_url", lambda _: None)
    monkeypatch.setattr(POOL, "connection", unexpected_db_access)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_app.app), base_url="http://test") as client:
            for suffix in ("media", "asset"):
                response = await client.get(f"/api/feed/42/{suffix}")
                assert response.status_code == 404
                assert response.headers["cache-control"] == "no-store"

    asyncio.run(request())


def test_asset_404_clears_resolution_cache_without_marking_dead(monkeypatch):
    from src.services.media import MediaUpstreamError
    from src.db import POOL

    def resolved(_):
        return ResolvedMedia("image", "https://cdn.goyangi.pics/stale.webp")

    cleared = []
    resolved.cache_clear = lambda: cleared.append(True)

    def missing(*args, **kwargs):
        raise MediaUpstreamError("Upstream host returned HTTP 404", status_code=404)

    def unexpected_db_access():
        raise AssertionError("Temporary media failures must not mutate database state")

    monkeypatch.setattr(web_app, "get_live_content_url", lambda _: "https://goyangi.pics/v/source.webp")
    monkeypatch.setattr(web_app, "resolve_media_url_cached", resolved)
    monkeypatch.setattr(web_app, "open_media_stream", missing)
    monkeypatch.setattr(POOL, "connection", unexpected_db_access)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_app.app), base_url="http://test") as client:
            return await client.get("/api/feed/42/asset")

    response = asyncio.run(request())
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert cleared == [True]


def test_public_search_pages_load_shared_navigation_and_use_q():
    from html.parser import HTMLParser

    class PageControls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.search_names = []
            self.scripts = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "input" and attrs.get("type") == "search":
                self.search_names.append(attrs.get("name"))
            if tag == "script":
                self.scripts.append(attrs.get("src", ""))

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for path in ("/feed", "/sets", "/scroll", "/photos", "/sorter", "/leaderboard"):
                response = await client.get(path)
                assert response.status_code == 200
                page = PageControls()
                page.feed(response.text)
                assert page.search_names == ([] if path == "/leaderboard" else ["q"])
                helper = next(i for i, src in enumerate(page.scripts) if src.startswith("/static/site-navigation.js"))
                assert helper < len(page.scripts) - 1

    asyncio.run(request())


def test_public_search_apis_limit_q_length():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for path in ("/api/feed", "/api/sets", "/api/scroll"):
                response = await client.get(path, params={"q": "x" * 101})
                assert response.status_code == 422
                assert response.json()["detail"][0]["loc"] == ["query", "q"]

    asyncio.run(request())
