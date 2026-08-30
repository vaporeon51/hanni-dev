from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from src.db.feedback import ContentFeedback
from src.db.feed import FeedItem
from src.db.collections import ContentCollection, ContentSet, CollectionPreview
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
            return await client.get("/api/sets?query=hanni&sort=latest&limit=1")

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
            return await client.get("/api/scroll?query=hanni&limit=8")

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
    assert '<a href="/sets">sets</a>' in response.text
    assert '<a href="/scroll">scroll</a>' in response.text
    assert '<span aria-current="page">feed</span>' in response.text
    assert '/static/analytics.js?v=' in response.text
    assert 'id="collection-heading"' in response.text
    assert '/static/app.css?v=' in response.text
    assert '/static/app.js?v=' in response.text
    assert "fonts.googleapis.com" not in response.text
    css = (web_app.REPO_ROOT / "static" / "app.css").read_text()
    assert 'font-family: Georgia, "Times New Roman", serif;' in css
    assert "font-weight: 400;" in css
    assert ".card-media video" in css
    assert ".card-media img, .card-media video" in css
    assert "clip-path: inset(0 round 11px)" in css
    assert "width: auto" in css
    assert "max-width: min(100%, 520px)" in css
    assert "width: min(560px, 100%)" in css
    assert "max-height: min(62vh, 540px)" in css
    assert "max-height: var(--mobile-media-max-height)" in css
    assert ".card-actions .upvote, .card-actions .downvote { min-width: 42px; }" in css
    assert ".collection-link" in css
    assert '<option value="random" selected>random</option>' in response.text
    assert '<option value="top">top</option>' in response.text
    assert 'id="limit"' not in response.text
    assert 'id="feed-sentinel"' in response.text
    assert 'id="timeline-tools"' in response.text
    assert '<svg class="timeline-icon timeline-icon-search"' in response.text
    assert '<svg class="timeline-icon timeline-icon-refresh"' in response.text
    assert '<svg class="timeline-icon timeline-icon-top"' in response.text
    assert "stroke-width: 2.35;" in css
    assert "appearance: none;" in css
    assert "background-position: right 13px center;" in css
    assert "Loading little links" not in response.text
    assert "a tiny corner for good links" not in response.text
    assert "autofeed" not in response.text.lower()
    assert response.text.index('id="feed"') < response.text.index('class="feed-status"')


def test_sets_page_renders_separately():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/sets")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert "search sets by member or group" in response.text
    assert '/static/sets.js?v=' in response.text
    assert '<option value="latest" selected>newest</option>' in response.text
    assert '<option value="oldest">oldest</option>' in response.text
    assert '<option value="random"' not in response.text
    assert '<option value="top"' not in response.text
    assert 'id="limit"' not in response.text
    assert 'id="feed-sentinel"' in response.text
    assert 'id="timeline-tools"' in response.text
    assert '<svg class="timeline-icon timeline-icon-search"' in response.text
    script = (web_app.REPO_ROOT / "static" / "sets.js").read_text()
    assert "const BATCH_SIZE = 5;" in script
    assert "setEndObserver" in script
    assert "async function loadMoreSets()" in script
    assert "cursor: state.nextCursor" in script
    assert not script.rstrip().endswith("loadSets();")


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


def test_scroll_client_is_bounded_and_supports_desktop_paging():
    script = (web_app.REPO_ROOT / "static" / "scroll.js").read_text()
    css = (web_app.REPO_ROOT / "static" / "scroll.css").read_text()

    assert "MAX_MOUNTED_REELS = 24" in script
    assert "CLIENT_HISTORY_CAPACITY = 100" in script
    assert "function trimMountedCards()" in script
    assert "function scheduleMountedCardTrim()" in script
    assert 'addEventListener("wheel"' in script
    assert 'addEventListener("keydown"' in script
    assert "gestureAlreadyHandled" in script
    wheel_handler = script[script.index('$("reel-feed").addEventListener("wheel"'):]
    assert wheel_handler.index("event.preventDefault();") < wheel_handler.index("Math.abs(event.deltaY) < 12")
    assert "}, 720);" in script
    assert "state.spacerHeight += removedHeight" in script
    assert "top: target.offsetTop" in script
    assert "target.scrollIntoView" not in script
    assert '{ showLabel: false }' in script
    assert "scroll-snap-type: y mandatory" in css
    assert "@media (hover: hover) and (pointer: fine)" in css
    assert "overscroll-behavior-y: none" in css
    assert "scroll-snap-type: none" in css
    assert "object-fit: contain" in css
    assert "max-width: 100%" in css
    assert "max-height: 100%" in css
    assert "const fitInsideStage = () =>" in script
    assert "availableWidth / intrinsicWidth" in script
    assert "availableHeight / intrinsicHeight" in script
    assert "new ResizeObserver(fitInsideStage)" in script
    assert 'thumbIcon("up")' in script
    assert 'thumbIcon("down")' in script
    assert '"Upvote this link"' in script
    assert '"Downvote this link"' in script


def test_client_loads_timeline_batches_and_autoplays_video():
    script = (web_app.REPO_ROOT / "static" / "app.js").read_text()

    assert script.rstrip().endswith("else clearFeed();")
    assert "const BATCH_SIZE = 5;" in script
    assert 'sort: state.sort' in script
    assert '$("sort").value' in script
    assert 'state.sort !== "random"' in script
    assert '$("limit")' not in script
    assert "media.autoplay = true" in script
    assert "videoPlaybackObserver" in script
    assert "mediaWindowObserver" in script
    assert "feedEndObserver" in script
    assert "async function loadMoreFeed()" in script
    assert "CONTINUATION_GAP_MS = 1100" in script
    assert "state.continuationTimer" in script
    assert "state.retryContinuation" in script
    assert "const initialCollectionId = initializeHistory();" in script
    assert "else loadFeed();" not in script
    assert "function lockMobileMediaHeight()" in script
    assert 'window.addEventListener("orientationchange"' in script
    assert "scrollIntoView" in script
    assert '$("query").blur()' in script
    assert 'search.scrollIntoView' in script
    assert 'query").focus({ preventScroll: true })' in script
    assert 'feedbackButton("upvote", "upvote", "↑", "Upvote this link")' in script
    assert 'feedbackButton("downvote", "downvote", "↓", "Downvote this link")' in script
    assert 'feedbackButton("report", "report", "report", "Report wrong idol")' in script
    assert 'data-count="upvotes"' not in script
    assert 'data-count="downvotes"' not in script
    assert 'const reportReason = action === "report" ? "wrong_idol" : "";' in script
    assert 'select[data-action="report"]' not in script
    assert '["dead_link", "dead link"]' not in script
    assert "dead link report ${payload.dead_link_reports} of 3" not in script
    assert "state.visibleCount" not in script
    assert "REVEAL_DELAY_MS" not in script
    assert "function disposeView(snapshot)" in script
    assert 'media.removeAttribute("src")' in script
    assert "dataset.mediaSrc" not in script
    assert "mediaCandidates" not in script
    assert "if (copied) await recordImplicitUpvote(card, id);" in script
    assert "async function recordImplicitUpvote(card, id)" in script
    assert "async function loadCollection(contentLinkId)" in script
    assert "function navigateToCollection(contentLinkId, href)" in script
    assert 'window.addEventListener("popstate"' in script
    assert "VIEW_CACHE_CAPACITY = 1" in script
    assert script.index("const scrollY = window.scrollY;") < script.index(
        "nodes.forEach((node) => node.remove());"
    )
    assert "view set (${count}) →" in script
    assert "?collection=${item.content_link_id}" in script
