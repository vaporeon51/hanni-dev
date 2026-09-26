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


def _post_vote(monkeypatch, payload, distinct=1, is_new=True):
    recorded = {}

    def fake_record(winner_id, loser_id, k):
        recorded["args"] = (winner_id, loser_id, k)
        return {"winner_delta": 4, "loser_delta": -4}

    monkeypatch.setattr(web_app, "record_sorter_vote", fake_record)
    monkeypatch.setattr(
        web_app, "register_pair_vote", lambda token, day, pair: (distinct, is_new)
    )

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
    assert response.json() == {"recorded": True, "global_k": 8}
    assert recorded["args"] == ("role-a", "role-b", 8)


def test_sorter_vote_damps_k_after_marathon(monkeypatch):
    response, recorded = _post_vote(
        monkeypatch, {"winner_role_id": "role-a", "loser_role_id": "role-b"},
        distinct=1001,
    )
    assert response.status_code == 200
    assert response.json() == {"recorded": True, "global_k": 2}
    assert recorded["args"] == ("role-a", "role-b", 2)


def test_sorter_vote_stops_farmed_rematches(monkeypatch):
    response, recorded = _post_vote(
        monkeypatch, {"winner_role_id": "role-a", "loser_role_id": "role-b"},
        distinct=1, is_new=False,
    )
    assert response.status_code == 200
    assert response.json() == {"recorded": True, "global_k": 0}
    assert recorded["args"] == ("role-a", "role-b", 0)


def test_pair_key_is_order_independent():
    assert bias.pair_key_for("role-b", "role-a") == bias.pair_key_for("role-a", "role-b")


def test_scaled_global_k_decays_to_floor():
    assert bias.scaled_global_k(0) == 8
    assert bias.scaled_global_k(-5) == 8
    assert bias.scaled_global_k(250) == 4
    assert bias.scaled_global_k(750) == 2
    assert bias.scaled_global_k(100000) == 1
    assert all(
        bias.scaled_global_k(n + 1) <= bias.scaled_global_k(n) for n in range(0, 3000)
    )


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
        web_app, "register_pair_vote", lambda token, day, pair: (1, True)
    )
    monkeypatch.setattr(
        web_app, "record_sorter_vote",
        lambda winner_id, loser_id, k: {"ok": True},
    )
    first, second = asyncio.run(request())
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == {"recorded": False}


def test_leaderboard_limit_param_caps_entries(monkeypatch):
    seen_idols = []
    seen_groups = []

    def fake_idols(limit):
        seen_idols.append(limit)
        return bias.Leaderboard(
            entries=[
                bias.LeaderboardEntry(f"r-{i}", f"M{i}", "G", 1200 + i, "img", None, 100, i + 1)
                for i in range(5)
            ][:limit],
            vote_count=500,
            movement_baseline_date=None,
        )

    def fake_groups(limit, top_n):
        seen_groups.append((limit, top_n))
        return bias.GroupLeaderboard(
            entries=[
                bias.GroupLeaderboardEntry(f"g{i}", 1300, 4, 3, ["A"], "img", 100, ["img"], 1350, i + 1)
                for i in range(5)
            ][:limit],
            vote_count=500,
            top_n=top_n,
        )

    monkeypatch.setattr(web_app, "get_global_leaderboard", fake_idols)
    monkeypatch.setattr(web_app, "get_global_group_leaderboard", fake_groups)

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            default_idols = await client.get("/api/leaderboard?kind=idols")
            two_idols = await client.get("/api/leaderboard?kind=idols&limit=2")
            default_groups = await client.get("/api/leaderboard?kind=groups")
            many_groups = await client.get("/api/leaderboard?kind=groups&limit=200")
            too_many = await client.get("/api/leaderboard?kind=idols&limit=501")
            return default_idols, two_idols, default_groups, many_groups, too_many

    default_idols, two_idols, default_groups, many_groups, too_many = asyncio.run(request())
    assert seen_idols[0] == bias.LEADERBOARD_SNAPSHOT_LIMIT
    assert len(default_idols.json()["entries"]) == 5  # fake only has 5
    assert seen_idols[1] == 2
    assert len(two_idols.json()["entries"]) == 2
    assert seen_groups[0] == (15, 3)
    assert len(default_groups.json()["entries"]) == 5  # fake only has 5
    assert seen_groups[1] == (200, 3)
    assert len(many_groups.json()["entries"]) == 5  # fake only has 5
    assert too_many.status_code == 422


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
            bias.LeaderboardEntry("role-1", "Hanni", "NewJeans", 1284, "https://img/1.jpg", None, 420, 1),
            bias.LeaderboardEntry("role-2", "Minji", "NewJeans", 1270, "https://img/2.jpg", 1, 380, 2),
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
    assert payload["entries"][0]["provisional"] is False
    assert payload["entries"][1]["rank"] == 2
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
                1520, 1,
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
    assert entry["rank"] == 1
    assert entry["provisional"] is False
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
    assert board.entries[0].rank == 1
    assert board.entries[0].provisional is False


def test_prune_visitor_pair_votes_deletes_only_stale_days(monkeypatch):
    executed = {}

    class FakeCursor:
        rowcount = 41

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params):
            executed["query"] = query
            executed["params"] = params

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return FakeCursor()

    class FakePool:
        def connection(self):
            return FakeConnection()

    monkeypatch.setattr(bias, "_pool", lambda: FakePool())

    assert bias.prune_visitor_pair_votes() == 41
    assert "DELETE FROM visitor_pair_votes" in executed["query"]
    assert "day < CURRENT_DATE - %s" in executed["query"]
    assert executed["params"] == (2,)
    assert bias.prune_visitor_pair_votes(-5) == 41
    assert executed["params"] == (0,)


def test_shrunk_elo_pulls_thin_evidence_to_prior():
    assert bias.shrunk_elo(1300, 200) == 1293
    assert bias.shrunk_elo(1250, 3) == 1208
    assert bias.shrunk_elo(1200, 0) == 1200
    assert bias.shrunk_elo(1150, 60) == 1160
    assert bias.shrunk_elo(1400, 10**9) == 1400


def test_idol_board_ranks_shrunk_and_flags_fresh():
    board = bias._build_leaderboard(
        [
            ("r-hot", "Hot", "G", 1500, "img", None, 200, None, 1493),
            ("r-new", "New", "G", 1500, "img", None, 3, None, 1233),
            ("r-solid", "Solid", "G", 1300, "img", None, 100, None, 1287),
        ],
        303,
    )
    assert [e.member_name for e in board.entries] == ["Hot", "New", "Solid"]
    assert board.entries[0].rank == 1
    assert board.entries[1].provisional is True
    assert board.entries[1].rank is None
    # Ranked-only numbering skips the fresh entry.
    assert board.entries[2].rank == 2


def test_group_board_flags_thin_groups():
    board = bias._build_group_leaderboard(
        [("big", 1400, 5, 3, ["A"], "img", ["img"], 900, 1450),
         ("tiny", 1400, 5, 3, ["B"], "img", ["img"], 9, 1450)],
        909,
        3,
    )
    assert board.entries[0].provisional is False
    assert board.entries[0].rank == 1
    assert board.entries[1].provisional is True
    assert board.entries[1].rank is None


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


def test_sorter_catalog_fifty_fifty_second_generation_split():
    import json

    catalog = json.loads(
        (web_app.REPO_ROOT / "static" / "sorter" / "catalog.json").read_text()
    )
    members = [
        e for e in catalog["entries"]
        if e.get("kind") == "idol" and "FIFTY FIFTY" in (e.get("groups") or [])
    ]
    assert sorted(e["short"] for e in members) == [
        "Athena", "Chanelle", "Hana", "Keena", "Yewon",
    ]
    for entry in members:
        # Keena debuted November 2022; the other four debuted September 2024.
        assert entry["gen"] == (["gen4"] if entry["short"] == "Keena" else ["gen5"])
    group_def = next(g for g in catalog["groups"] if g["key"] == "FIFTY FIFTY")
    assert group_def["gen"] == ["gen5"]


def test_sorter_catalog_hand_picked_portrait():
    import json

    catalog = json.loads(
        (web_app.REPO_ROOT / "static" / "sorter" / "catalog.json").read_text()
    )
    by_name = {e.get("name"): e for e in catalog["entries"]}
    role_photos = json.loads(
        (web_app.REPO_ROOT / "static" / "sorter" / "role-photos.json").read_text()
    )
    handpicked = (
        ("tripleS Kim Yooyeon", "1079677939878219826",
         "/static/sorter/idols/tripleS-kim-yooyeon.jpg"),
        ("tripleS Kim Chaeyeon", "1000867801420009502",
         "/static/sorter/idols/tripleS-kim-chaeyeon.jpg"),
        ("tripleS Yoon Seoyeon", "1141805269978980452",
         "/static/sorter/idols/tripleS-yoon-seoyeon.webp"),
        ("tripleS Hayeon", "1313202769938878514",
         "/static/sorter/idols/tripleS-hayeon.jpg"),
    )
    for name, role_id, local in handpicked:
        # Sorter serves the vendored file (catalog has no role_id to embed).
        assert by_name[name]["local"] == local
        assert (web_app.REPO_ROOT / local.lstrip("/")).exists()
        # Board: no embed harvested (see BACKFILL_EXCLUDE), so ROLE_PHOTOS wins.
        assert role_photos[role_id] == local
        assert web_app._board_image(role_id, "https://cdn.example.com/db.jpg") == local
    # Sorter-only idols (no board entry): vendored file wins by default.
    sorter_only = (
        ("UNCHILD Tina", "/static/sorter/idols/unchild-tina.webp"),
    )
    for name, local in sorter_only:
        assert by_name[name]["local"] == local
        assert (web_app.REPO_ROOT / local.lstrip("/")).exists()


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
