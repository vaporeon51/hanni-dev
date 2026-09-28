from __future__ import annotations

import asyncio

import httpx
import pytest

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


BALLOT_ID = "f2b630f5-c018-4bfd-a8b5-268f4064858b"


def post_ballot(payload):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_app.app), base_url="http://test") as client:
            return await client.post("/api/sorter/ballot", json=payload)
    return asyncio.run(request())


def test_ballot_endpoint_passes_complete_evidence(monkeypatch):
    calls = []
    def record(*args):
        calls.append(args)
        return {"recorded": True, "comparisons": 2}
    monkeypatch.setattr(web_app, "record_sorter_ballot", record)
    pairs = [["a", "b", 1], ["b", "c", .5]]
    response = post_ballot({"ballot_id": BALLOT_ID, "comparisons": pairs})
    assert response.json()["recorded"]
    assert calls[0][:2] == (BALLOT_ID, pairs)
    assert calls[0][2]


@pytest.mark.parametrize("payload", [None, {}, {"ballot_id": "bad", "comparisons": [["a", "b", 1]]},
    *[{"ballot_id": BALLOT_ID, "comparisons": pairs} for pairs in
      ([], [["a", "a", 1]], [["a", "b", True]], [["a", "b", 2]], [["a", "b"]], [["a", "b", 1]] * 20001)]])
def test_ballot_endpoint_rejects_invalid_payload(payload):
    assert post_ballot(payload).json()["reason"] == "invalid_payload"


def test_ballot_endpoint_fails_closed(monkeypatch):
    def fail(*args):
        raise RuntimeError("offline")
    monkeypatch.setattr(web_app, "record_sorter_ballot", fail)
    assert post_ballot({"ballot_id": BALLOT_ID, "comparisons": [["a", "b", 1]]}).json()["reason"] == "unavailable"


def test_old_click_endpoint_cannot_spend_budget():
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_app.app), base_url="http://test") as client:
            return await client.post("/api/sorter/vote", json={"winner_role_id": "a", "loser_role_id": "b"})
    assert asyncio.run(request()).json()["reason"] == "completed_sort_required"


def test_ballot_combines_late_losses_before_cap():
    from decimal import Decimal
    ratings = {i: 1200 for i in "abcde"}
    pairs = [("a", "b", 1), ("a", "c", 1), ("a", "d", 0), ("a", "e", 0)]
    result = bias.ballot_deltas(ratings, pairs, {"a": Decimal(11)})
    assert result["a"] == 0
    assert result["b"] < 0 and result["e"] > 0
    assert sum(result.values()) == 0
    assert result == bias.ballot_deltas(ratings, list(reversed(pairs)), {"a": Decimal(11)})


def test_long_ballots_do_not_buy_more_influence():
    ratings = {str(i): 1200 for i in range(101)}
    result = bias.ballot_deltas(ratings, [("0", str(i), 1) for i in range(1, 101)], {})
    assert result["0"] == 4
    assert sum(result.values()) == 0


def test_ballot_caps_net_movement_and_balances_exactly():
    from decimal import Decimal
    import random
    rng = random.Random(12)
    for _ in range(100):
        ratings = {str(i): rng.randrange(900, 1600) for i in range(20)}
        spent = {i: Decimal(str(rng.uniform(0, 12))) for i in ratings}
        spent["0"] = Decimal(12)
        pairs = [(a, b, rng.choice([0, .5, 1])) for a in ratings for b in ratings if a < b]
        result = bias.ballot_deltas(ratings, pairs, spent)
        assert sum(result.values()) == 0
        assert result["0"] == 0
        assert all(abs(d) <= min(8, 12 - spent[i]) for i, d in result.items())


def test_ties_move_unequal_ratings_toward_each_other():
    result = bias.ballot_deltas({"a": 1400, "b": 1200}, [("a", "b", .5)], {})
    assert result["a"] < 0 < result["b"]
    assert sum(result.values()) == 0


def test_leaderboard_limit_param_caps_entries(monkeypatch):
    seen_idols = []
    seen_groups = []

    def fake_idols(limit, include_provisional=True):
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
    monkeypatch.setattr(web_app, "get_global_leaderboard", lambda limit, include_provisional=True: board)

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
    monkeypatch.setattr(web_app, "get_global_leaderboard", lambda limit, include_provisional=True: board)

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


def test_leaderboard_image_priority_is_vendored_then_embed_then_database(monkeypatch):
    import datetime

    board = bias.Leaderboard(
        entries=[
            bias.LeaderboardEntry(
                "role-both", "Karina", "aespa",
                1522, "https://legacy.kpopping.com/blocked.jpg", None,
            ),
            bias.LeaderboardEntry(
                "role-embed", "Hanni", "NewJeans",
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
    monkeypatch.setattr(web_app, "get_global_leaderboard", lambda limit, include_provisional=True: board)
    monkeypatch.setattr(
        web_app, "EMBED_PHOTOS", {
            "role-both": "https://images-ext-1.discordapp.net/external/SIG/x",
            "role-embed": "https://images-ext-1.discordapp.net/external/SIG/y",
        }
    )
    monkeypatch.setattr(
        web_app, "ROLE_PHOTOS", {
            "role-both": "/static/sorter/idols/abc123.jpg",
        }
    )

    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/leaderboard?scope=global&kind=idols")

    response = asyncio.run(request())
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert entries[0]["image_url"] == "/static/sorter/idols/abc123.jpg"
    assert entries[1]["image_url"] == "https://images-ext-1.discordapp.net/external/SIG/y"
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
    assert "DELETE FROM visitor_idol_budget" in executed["query"]
    assert "day < CURRENT_DATE - %s" in executed["query"]
    assert executed["params"] == (2,)
    assert bias.prune_visitor_pair_votes(-5) == 41
    assert executed["params"] == (0,)


def test_idol_board_uses_recorded_matchups_for_eligibility():
    board = bias._build_leaderboard(
        [
            ("r-hot", "Hot", "G", 1500, "img", None, 200, None, 1500),
            ("r-new", "New", "G", 1200, "img", None, 0, None, 1200),
            ("r-solid", "Solid", "G", 1300, "img", None, 1, None, 1300),
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


def test_sorter_catalog_stripped_names_stay_linked():
    import json

    catalog = json.loads(
        (web_app.REPO_ROOT / "static" / "sorter" / "catalog.json").read_text()
    )
    by_name = {e.get("name"): e for e in catalog["entries"]}
    # Display strips must still link: sorter votes count under these roles.
    assert by_name["tripleS Kim Yooyeon"]["role_id"] == "1079677939878219826"
    assert by_name["tripleS Kim Nakyoung"]["role_id"] == "1221925113029464094"
    # Full-display names link to their own group's row on real collisions.
    assert by_name["tripleS Kim Chaeyeon"]["role_id"] == "1000867801420009502"
    assert by_name["tripleS Yoon Seoyeon"]["role_id"] == "1141805269978980452"


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
         "/static/sorter/idols/tripleS-yoon-seoyeon.jpg"),
        ("tripleS Hayeon", "1313202769938878514",
         "/static/sorter/idols/tripleS-hayeon.jpg"),
        ("IVE Liz", "916036552327594005",
         "/static/sorter/idols/ive-liz.jpg"),
        ("NewJeans Danielle", "1000865551020740629",
         "/static/sorter/idols/newjeans-danielle.jpg"),
        ("tripleS Dahyun", "1313202074607157268",
         "/static/sorter/idols/tripleS-dahyun.jpg"),
        ("tripleS Yeonji", "1234942669751455846",
         "/static/sorter/idols/tripleS-yeonji.jpg"),
        ("ILLIT Moka", "1147390143980908584",
         "/static/sorter/idols/illit-moka.jpg"),
    )
    for name, role_id, local in handpicked:
        # Sorter serves the vendored file (catalog has no role_id to embed).
        assert by_name[name]["local"] == local
        assert (web_app.REPO_ROOT / local.lstrip("/")).exists()
        # Board serves vendored portraits first, so ROLE_PHOTOS wins outright.
        assert role_photos[role_id] == local
        assert web_app._board_image(role_id, "https://cdn.example.com/db.jpg") == local
    # Sorter-only idols (no board entry): vendored file wins by default.
    sorter_only = (
        ("UNCHILD Tina", "/static/sorter/idols/unchild-tina.jpg"),
        ("VVS Brittney", "/static/sorter/idols/vvs-brittney.jpg"),
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
    assert 'href="/photos"' in response.text


def test_photos_page_renders():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/photos")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert "/static/photos.js?v=" in response.text
    assert "/static/photos.css?v=" in response.text
    assert 'id="wall"' in response.text
    assert 'id="search"' in response.text


def test_leaderboard_page_renders():
    async def request():
        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/leaderboard")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert "/static/leaderboard.js?v=" in response.text
    assert "/static/sorter/engine.js?v=" not in response.text
    assert 'data-scope' not in response.text
    assert 'data-kind="groups"' in response.text


@pytest.mark.parametrize("payload", [None, [], "hello", 1, True, {}])
def test_sorter_invalid_json_shapes_fail_closed(monkeypatch, payload):
    response = post_ballot(payload)
    assert response.status_code == 200
    assert response.json() == {"recorded": False, "reason": "invalid_payload"}


def test_sorter_unknown_id_is_observable_without_logging_identity(monkeypatch, caplog):
    monkeypatch.setattr(web_app, "record_sorter_ballot", lambda *args: {"recorded": False, "reason": "unknown_id"})
    response = post_ballot({"ballot_id": BALLOT_ID, "comparisons": [["missing-secret-marker", "r1", 1]]})
    assert response.json()["reason"] == "unknown_id"
    assert "check catalog sync" in caplog.text
    assert "missing-secret-marker" not in caplog.text


def test_sorter_burst_limiter_allows_bursts_refills_and_bounds_memory(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(web_app.time, "monotonic", lambda: now[0])
    limiter = web_app._SorterBurstLimiter(burst=12, per_second=4, capacity=2)
    assert all(limiter.allow("alice") for _ in range(12))
    assert not limiter.allow("alice")
    now[0] += .25
    assert limiter.allow("alice")
    assert not limiter.allow("alice")
    assert limiter.allow("bob") and limiter.allow("charlie")
    assert len(limiter._entries) == 2


def test_rate_limited_vote_never_calls_database(monkeypatch):
    class Deny:
        def allow(self, key):
            return False
    monkeypatch.setattr(web_app, "_sorter_burst_limiter", Deny())
    monkeypatch.setattr(web_app, "record_sorter_ballot", lambda *args: pytest.fail("database called"))
    response = post_ballot({"ballot_id": BALLOT_ID, "comparisons": [["a", "b", 1]]})
    assert response.json() == {"recorded": False, "reason": "rate_limited"}


def test_sorter_can_opt_out_of_provisional_sample(monkeypatch):
    seen = []
    def board(limit, include_provisional=True):
        seen.append((limit, include_provisional))
        return bias.Leaderboard([], 0)
    monkeypatch.setattr(web_app, "get_global_leaderboard", board)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_app.app), base_url="http://test") as client:
            return await client.get('/api/leaderboard?kind=idols&limit=200&include_provisional=false')
    assert asyncio.run(request()).status_code == 200
    assert seen == [(200, False)]
