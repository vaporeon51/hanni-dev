from __future__ import annotations

import asyncio

import httpx

from src.db import bias
from src.web import app as web_app

CLEAN_HOST = "bias.hannibee.art"


def _get(path: str, host: str | None = None, method: str = "GET"):
    async def request():
        headers = {"host": host} if host else None
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            return await client.request(method, path)

    return asyncio.run(request())


def test_clean_home_has_no_nsfw():
    response = _get("/", host=CLEAN_HOST)

    assert response.status_code == 200
    assert '<a class="menu-card" href="/sorter">' in response.text
    assert '<a class="menu-card" href="/leaderboard">' in response.text
    assert "nsfw-cluster" not in response.text
    assert 'href="/feed"' not in response.text
    assert 'href="/sets"' not in response.text
    assert 'href="/scroll"' not in response.text
    assert "age-gate" not in response.text
    assert "#i-flame" not in response.text
    assert "18+" not in response.text
    assert "unwholesomely" not in response.text
    assert "feed, sets, and scroll" not in response.text


def test_main_home_still_full():
    response = _get("/")

    assert response.status_code == 200
    assert "nsfw-cluster" in response.text
    assert 'href="/feed"' in response.text


def test_clean_nsfw_pages_bounce_home():
    for path in ("/feed", "/sets", "/scroll"):
        response = _get(path, host=CLEAN_HOST)

        assert response.status_code == 307
        assert response.headers["location"] == "/"


def test_clean_content_apis_404():
    paths = [
        ("/api/feed", "GET"),
        ("/api/scroll", "GET"),
        ("/api/sets", "GET"),
        ("/api/collections/1", "GET"),
        ("/api/collections/by-url?url=https://example.com/x", "GET"),
        ("/api/link?url=https://example.com/x", "GET"),
        ("/api/feed/1/media", "GET"),
        ("/api/roles?q=hanni", "GET"),
        ("/api/feed/1/vote/up", "POST"),
        ("/api/feed/1/report", "POST"),
    ]
    for path, method in paths:
        response = _get(path, host=CLEAN_HOST, method=method)

        assert response.status_code == 404, path


def test_clean_wholesome_still_served(monkeypatch):
    board = bias.Leaderboard(entries=[], vote_count=0, movement_baseline_date=None)
    monkeypatch.setattr(web_app, "get_global_leaderboard", lambda limit: board)

    sorter = _get("/sorter", host=CLEAN_HOST)
    assert sorter.status_code == 200
    assert "nsfw-cluster" not in sorter.text

    leaderboard = _get("/leaderboard", host=CLEAN_HOST)
    assert leaderboard.status_code == 200
    assert "nsfw-cluster" not in leaderboard.text

    api = _get("/api/leaderboard?kind=idols", host=CLEAN_HOST)
    assert api.status_code == 200
    assert api.json()["scope"] == "global"

    assert _get("/healthz", host=CLEAN_HOST).status_code == 200


def test_clean_sorter_votes_still_count(monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        web_app, "record_sorter_vote",
        lambda winner_id, loser_id: recorded.setdefault("args", (winner_id, loser_id)) or {"winner_delta": 4, "loser_delta": -4},
    )

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"host": CLEAN_HOST},
        ) as client:
            return await client.post(
                "/api/sorter/vote",
                json={"winner_role_id": "role-a", "loser_role_id": "role-b"},
            )

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json() == {"recorded": True}
    assert recorded["args"] == ("role-a", "role-b")


def test_clean_host_match_ignores_port():
    response = _get("/", host="bias.hannibee.art:8000")
    assert response.status_code == 200
    assert "nsfw-cluster" not in response.text

    response = _get("/", host="hannibee.art:8000")
    assert response.status_code == 200
    assert "nsfw-cluster" in response.text
