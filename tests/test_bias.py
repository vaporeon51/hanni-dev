from __future__ import annotations

import asyncio

import httpx

from src.db import bias
from src.web import app as web_app


def test_calculate_elo_delta_equal_ratings():
    winner, loser = bias.calculate_elo_delta(1200, 1200, bias.GLOBAL_ELO_K)
    assert winner == 4
    assert loser == -4


def test_calculate_elo_delta_upset_moves_more():
    upset, _ = bias.calculate_elo_delta(1000, 1400, bias.GLOBAL_ELO_K)
    expected, _ = bias.calculate_elo_delta(1400, 1000, bias.GLOBAL_ELO_K)
    assert upset > expected


def test_format_movement_tokens():
    assert bias.format_movement(None, 3, True) == "NEW"
    assert bias.format_movement(1, 3, True) == "▼2"
    assert bias.format_movement(5, 2, True) == "▲3"
    assert bias.format_movement(2, 2, True) == "–"
    assert bias.format_movement(1, 1, False) is None
    assert bias.format_movement(None, 1, False) is None


def test_record_sorter_vote_rejects_bad_ids():
    assert bias.record_sorter_vote("", "role-2") is None
    assert bias.record_sorter_vote("role-1", "role-1") is None
    assert bias.record_sorter_vote("", "") is None


def _post_vote(monkeypatch, payload):
    recorded = {}

    def fake_record(winner_id, loser_id):
        recorded["args"] = (winner_id, loser_id)
        return {"winner_delta": 4, "loser_delta": -4}

    monkeypatch.setattr(web_app, "record_sorter_vote", fake_record)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/sorter/vote", json=payload)

    return asyncio.run(request()), recorded


def test_sorter_vote_records_matchup(monkeypatch):
    response, recorded = _post_vote(
        monkeypatch, {"winner_role_id": "role-a", "loser_role_id": "role-b"}
    )
    assert response.status_code == 200
    assert response.json() == {"recorded": True}
    assert recorded["args"] == ("role-a", "role-b")


def test_sorter_vote_rejects_same_idol(monkeypatch):
    response, recorded = _post_vote(
        monkeypatch, {"winner_role_id": "role-a", "loser_role_id": "role-a"}
    )
    assert response.status_code == 200
    assert response.json() == {"recorded": False}
    assert recorded == {}


def test_sorter_vote_rate_limits_rapid_beacons(monkeypatch):
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/api/sorter/vote",
                json={"winner_role_id": "role-a", "loser_role_id": "role-b"},
            )
            second = await client.post(
                "/api/sorter/vote",
                json={"winner_role_id": "role-a", "loser_role_id": "role-b"},
            )
            return first, second

    monkeypatch.setattr(
        web_app, "record_sorter_vote",
        lambda winner_id, loser_id: {"ok": True},
    )
    first, second = asyncio.run(request())
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == {"recorded": False}


def test_leaderboard_rejects_bad_kind_but_ignores_legacy_scope(monkeypatch):
    board = bias.Leaderboard(entries=[], vote_count=0, movement_baseline_date=None)
    monkeypatch.setattr(web_app, "get_global_leaderboard", lambda limit: board)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            bad_kind = await client.get("/api/leaderboard?kind=vibes")
            legacy_scope = await client.get("/api/leaderboard?scope=server")
            return bad_kind, legacy_scope

    bad_kind, legacy_scope = asyncio.run(request())
    assert bad_kind.status_code == 400
    assert legacy_scope.status_code == 200
    assert legacy_scope.json()["scope"] == "global"


def test_global_idol_leaderboard_serializes_movement(monkeypatch):
    board = bias.Leaderboard(
        entries=[
            bias.LeaderboardEntry("role-1", "Hanni", "NewJeans", 1284, "https://img/1.jpg", None, 420),
            bias.LeaderboardEntry("role-2", "Minji", "NewJeans", 1270, "https://img/2.jpg", 1, 380),
        ],
        vote_count=1234,
        movement_baseline_date=None,
    )
    # date needs isoformat; use a real date object
    import datetime

    board = bias.Leaderboard(
        entries=board.entries, vote_count=1234,
        movement_baseline_date=datetime.date(2026, 9, 14),
    )
    monkeypatch.setattr(web_app, "get_global_leaderboard", lambda limit: board)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/leaderboard?scope=global&kind=idols")

    response = asyncio.run(request())
    assert response.status_code == 200
    payload = response.json()
    assert payload["vote_count"] == 1234
    assert payload["movement_baseline_date"] == "2026-09-14"
    assert payload["entries"][0]["rank"] == 1
    assert payload["entries"][0]["previous_rank"] is None
    assert payload["entries"][0]["votes"] == 420
    assert payload["entries"][1]["previous_rank"] == 1
    assert payload["entries"][1]["votes"] == 380


def test_leaderboard_image_priority_is_embed_then_vendored_then_database(monkeypatch):
    import datetime

    board = bias.Leaderboard(
        entries=[
            bias.LeaderboardEntry(
                "role-embed", "Karina", "aespa",
                1522, "https://legacy.kpopping.com/blocked.jpg", None,
            ),
            bias.LeaderboardEntry(
                "role-local", "Hanni", "NewJeans",
                1284, "https://legacy.kpopping.com/blocked.jpg", None,
            ),
            bias.LeaderboardEntry(
                "role-db", "Minji", "NewJeans",
                1270, "https://cdn.example.com/fallback.jpg", None,
            ),
        ],
        vote_count=10,
        movement_baseline_date=datetime.date(2026, 9, 14),
    )
    monkeypatch.setattr(web_app, "get_global_leaderboard", lambda limit: board)
    monkeypatch.setattr(
        web_app, "EMBED_PHOTOS", {"role-embed": "https://images-ext-1.discordapp.net/external/SIG/x"}
    )
    monkeypatch.setattr(
        web_app, "ROLE_PHOTOS", {
            "role-embed": "/static/sorter/idols/abc123.jpg",
            "role-local": "/static/sorter/idols/def456.jpg",
        }
    )

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/leaderboard?scope=global&kind=idols")

    response = asyncio.run(request())
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert entries[0]["image_url"] == "https://images-ext-1.discordapp.net/external/SIG/x"
    assert entries[1]["image_url"] == "/static/sorter/idols/def456.jpg"
    assert entries[2]["image_url"] == "https://cdn.example.com/fallback.jpg"


def test_global_group_board_resolves_photos_and_members(monkeypatch):
    board = bias.GroupLeaderboard(
        entries=[
            bias.GroupLeaderboardEntry(
                "aespa", 1495, 4, 3, ["Karina", "Winter", "NingNing"],
                "https://legacy.kpopping.com/top.jpg", 3844,
                ["https://legacy.kpopping.com/k.jpg", "https://legacy.kpopping.com/w.jpg", None],
                1520,
            ),
        ],
        vote_count=74712,
        top_n=3,
    )
    monkeypatch.setattr(web_app, "get_global_group_leaderboard", lambda limit, top_n: board)
    monkeypatch.setattr(
        web_app, "GROUP_PHOTOS", {"aespa": "/static/sorter/idols/group-aespa.jpg"}
    )
    monkeypatch.setattr(
        web_app,
        "EMBED_SOURCES",
        {"https://legacy.kpopping.com/k.jpg": "https://images-ext-1.discordapp.net/external/K/x"},
    )

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/leaderboard?scope=global&kind=groups")

    response = asyncio.run(request())
    assert response.status_code == 200
    entry = response.json()["entries"][0]
    assert entry["image_url"] == "/static/sorter/idols/group-aespa.jpg"
    assert entry["votes"] == 3844
    assert entry["peak_elo"] == 1520
    assert entry["top_members"][0] == {
        "name": "Karina",
        "image_url": "https://images-ext-1.discordapp.net/external/K/x",
    }
    assert entry["top_members"][2] == {"name": "NingNing", "image_url": None}


def test_group_board_maps_peak_elo():
    board = bias._build_group_leaderboard(
        [("aespa", 1495, 4, 3, ["Karina"], "img", ["img"], 100, 1520)],
        50,
        3,
    )

    assert board.entries[0].peak_elo == 1520
    assert board.entries[0].elo == 1495


def test_sorter_catalog_manual_idols_present():
    import json

    catalog = json.loads(
        (web_app.REPO_ROOT / "static" / "sorter" / "catalog.json").read_text()
    )
    ids = [entry["id"] for entry in catalog["entries"]]
    assert len(set(ids)) == len(ids)
    hyunjin = [e for e in catalog["entries"] if e.get("name") == "LOOSSEMBLE Hyunjin"]
    assert len(hyunjin) == 1
    assert hyunjin[0]["role_id"] == "779826921613426708"
    assert "LOOSSEMBLE" in hyunjin[0]["groups"]
    loossemble = [
        e for e in catalog["entries"]
        if e.get("kind") == "idol" and "LOOSSEMBLE" in (e.get("groups") or [])
    ]
    assert len(loossemble) == 5


def test_sorter_page_renders():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/sorter")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert "/static/sorter/sorter.js?v=" in response.text
    assert 'id="pick-left"' in response.text
    assert 'id="help"' in response.text
    assert 'id="verify"' in response.text
    assert 'id="verify-back"' in response.text


def test_sorter_verify_round_is_wired():
    script = (web_app.REPO_ROOT / "static" / "sorter" / "sorter.js").read_text()
    engine = (web_app.REPO_ROOT / "static" / "sorter" / "engine.js").read_text()

    assert "applyVerifySwaps" in engine
    assert "facedPairs" in engine
    assert "verifyRounds" in script
    assert "buildVerifyPairs" in script
    assert "finishVerify" in script
    assert "facedPairKeys" in script
    assert "CHALLENGE_TOP" in script


def test_leaderboard_page_renders():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/leaderboard")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert "/static/leaderboard.js?v=" in response.text
    assert "/static/sorter/engine.js?v=" in response.text
    assert 'data-scope="personal"' in response.text
    assert 'data-kind="groups"' in response.text


def test_leaderboard_mine_tab_replays_sorter_session():
    script = (web_app.REPO_ROOT / "static" / "leaderboard.js").read_text()

    assert "bias-club-session-v1" in script
    assert "BiasSorter.replay" in script
    assert "/api/leaderboard?kind=" in script
    assert "scope=${scope}" not in script
