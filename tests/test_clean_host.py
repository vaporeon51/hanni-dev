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


def test_clean_root_goes_straight_to_sorter():
    response = _get("/", host=CLEAN_HOST)

    assert response.status_code == 307
    assert response.headers["location"] == "/sorter"


def test_main_home_still_full():
    response = _get("/")

    assert response.status_code == 200
    assert "nsfw-cluster" in response.text
    assert "wholesome</span>" in response.text
    assert '<a class="nav-home" href="/">hanni♡</a>' in response.text
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
    assert "wholesome</span>" not in sorter.text
    assert '<a class="nav-home" href="/sorter">' in sorter.text

    leaderboard = _get("/leaderboard", host=CLEAN_HOST)
    assert leaderboard.status_code == 200
    assert "nsfw-cluster" not in leaderboard.text
    assert "wholesome</span>" not in leaderboard.text

    api = _get("/api/leaderboard?kind=idols", host=CLEAN_HOST)
    assert api.status_code == 200
    assert api.json()["scope"] == "global"

    assert _get("/healthz", host=CLEAN_HOST).status_code == 200


def test_clean_sorter_votes_still_count(monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        web_app, "register_pair_vote", lambda token, day, pair: (1, True)
    )
    monkeypatch.setattr(
        web_app, "record_sorter_vote",
        lambda winner_id, loser_id, k: recorded.setdefault("args", (winner_id, loser_id, k)) or {"winner_delta": 4, "loser_delta": -4},
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
    assert response.json() == {"recorded": True, "global_k": 8}
    assert recorded["args"] == ("role-a", "role-b", 8)


def test_clean_host_match_ignores_port():
    response = _get("/", host="bias.hannibee.art:8000")
    assert response.status_code == 307
    assert response.headers["location"] == "/sorter"

    response = _get("/", host="hannibee.art:8000")
    assert response.status_code == 200
    assert "nsfw-cluster" in response.text
