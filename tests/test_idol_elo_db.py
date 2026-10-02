"""Real PostgreSQL regression tests. Set HANNI_TEST_DATABASE_URL to a local DB.

Each test owns a temporary schema; never use DATABASE_URL / production data.
"""
import datetime
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from src.db import bias
from src.sorter_catalog import attach_leaderboard_ids, catalog_rating_rows, ROLE_ALIASES
from scripts.sync_sorter_idols import sync_catalog_ratings, check_catalog_ratings

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / 'migrations/table_updates39.sql').read_text()
DAY = datetime.date(2026, 9, 26)
CATALOG_ROWS = catalog_rating_rows(json.loads((ROOT / 'static/sorter/catalog.json').read_text())['entries'])


def test_catalog_has_stable_identity_for_every_idol():
    entries = json.loads((ROOT / 'static/sorter/catalog.json').read_text())['entries']
    idols = [e for e in entries if e['kind'] == 'idol']
    assert all(e.get('leaderboard_id') for e in idols)
    ids = [e['leaderboard_id'] for e in idols]
    assert len(set(ids)) == len(ids) - 1  # Hyunjin has two group cards, one identity.
    hyunjin = [e for e in idols if e['name'] in ('LATENCY Hyunjin', 'LOOSSEMBLE Hyunjin')]
    assert len({e['leaderboard_id'] for e in hyunjin}) == 1
    before = json.dumps(entries)
    attach_leaderboard_ids(entries)
    assert json.dumps(entries) == before
    for e in idols:
        if e['name'] in ROLE_ALIASES:
            assert e['leaderboard_id'] == ROLE_ALIASES[e['name']]
    assert not any(e.get('leaderboard_id') for e in entries if e['kind'] == 'group')


def test_small_k_keeps_fractional_information():
    assert bias.calculate_elo_delta(1200, 1200, 1) == (.5, -.5)
    delta = bias.ballot_deltas({'a': 1200, 'b': 1200}, [('a', 'b', 1)], {'a': Decimal('11.75'), 'b': Decimal('10')})
    assert delta['a'] == Decimal('.25')


@pytest.fixture
def db(monkeypatch):
    url = os.getenv('HANNI_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set HANNI_TEST_DATABASE_URL to an isolated local Postgres database')
    host = psycopg.conninfo.conninfo_to_dict(url).get('host', '')
    assert host in ('localhost', '127.0.0.1') or host.startswith('/tmp/'), 'Local test DB only'
    schema = 'elo_test_' + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
    pool = ConnectionPool(url, min_size=1, max_size=12, open=True,
                          kwargs={'options': f'-c search_path={schema}'})
    try:
        with pool.connection() as conn:
            conn.execute('''CREATE TABLE role_info (
                role_id TEXT PRIMARY KEY, member_name TEXT, group_name TEXT, image_url TEXT,
                global_elo INTEGER NOT NULL DEFAULT 1200,
                global_win_count INTEGER NOT NULL DEFAULT 0,
                global_match_count INTEGER NOT NULL DEFAULT 0)''')
            with conn.cursor() as cur:
                cur.executemany('INSERT INTO role_info (role_id, member_name, group_name, image_url) VALUES (%s,%s,%s,%s)',
                                [(f'r{i}', f'Idol {i}', 'Test group', 'photo') for i in range(20)])
            conn.execute("INSERT INTO role_info VALUES ('779828161319534595','Chuu','LOONA','photo',1360,60,100)")
            conn.execute((ROOT / 'migrations/table_updates37.sql').read_text())
        with pool.connection() as conn:
            conn.execute(MIGRATION)
            conn.execute((ROOT / 'migrations/table_updates46.sql').read_text())
        with pool.connection() as conn:
            sync_catalog_ratings(conn, CATALOG_ROWS)
        monkeypatch.setattr(bias, '_pool', lambda: pool)
        yield pool
    finally:
        pool.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA {schema} CASCADE')


def record_vote(winner, loser, visitor, day):
    return bias.record_sorter_ballot(str(uuid.uuid4()), [(winner, loser, 1)], visitor, day)


def row(db, idol='r0'):
    with db.connection() as conn:
        return conn.execute('SELECT global_elo,global_match_count FROM idol_ratings WHERE role_id=%s', (idol,)).fetchone()


def test_migration_and_sync_preserve_displayed_score_and_register_catalog(db):
    assert row(db, '779828161319534595') == (1339, 100)
    old_leader = bias.get_global_leaderboard(1).entries[0]
    assert (old_leader.role_id, old_leader.elo, old_leader.provisional) == ('779828161319534595', 1339, False)
    entries = json.loads((ROOT / 'static/sorter/catalog.json').read_text())['entries']
    for e in entries:
        if e['kind'] == 'idol':
            assert row(db, e['leaderboard_id']) is not None
    record_vote('r0', 'r1', 'alice', DAY)
    before = row(db)
    with db.connection() as conn:
        conn.execute(MIGRATION)
    assert row(db) == before
    with db.connection() as conn:
        sync_catalog_ratings(conn, CATALOG_ROWS)
        assert conn.execute("SELECT global_elo FROM role_info WHERE role_id='779828161319534595'").fetchone()[0] == 1360
    assert row(db) == before
    assert record_vote('sorter:0', 'r0', 'new-person', DAY)['recorded']


def test_repeat_is_noop_and_next_day_has_new_budget(db):
    assert record_vote('r0', 'r1', 'alice', DAY)['recorded']
    before = row(db)
    assert not record_vote('r1', 'r0', 'alice', DAY)['recorded']
    assert row(db) == before
    record_vote('r0', 'r1', 'alice', DAY + datetime.timedelta(days=1))
    assert row(db)[1] == 2


def test_many_opponents_share_daily_absolute_cap(db):
    for i in range(1, 20):
        record_vote('r0', f'r{i}', 'alice', DAY)
    assert row(db)[0] == 1212
    with db.connection() as conn:
        assert conn.execute("SELECT MAX(spent) FROM visitor_idol_budget").fetchone()[0] == 12
        assert conn.execute("SELECT SUM(global_elo) FROM idol_ratings WHERE role_id LIKE 'r%'").fetchone()[0] == 20 * 1200


def test_budget_is_absolute_and_respects_both_sides(db):
    record_vote('r0', 'r1', 'alice', DAY)
    record_vote('r2', 'r0', 'alice', DAY)
    for i in range(3, 20):
        record_vote('r0', f'r{i}', 'alice', DAY)
    with db.connection() as conn:
        spent = conn.execute("SELECT spent FROM visitor_idol_budget WHERE visitor_token=%s AND role_id='r0'", (bias.visitor_key("alice"),)).fetchone()[0]
    assert spent == 12
    assert abs(row(db)[0] - 1200) < 12
    # An exhausted loser also blocks a fresh winner; no extra counts.
    before = row(db, 'r19')
    assert not record_vote('r19', 'r0', 'alice', DAY)['recorded']
    assert row(db, 'r19') == before


def test_concurrent_votes_cannot_exceed_budget_or_double_count(db):
    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(lambda i: record_vote('r0', f'r{i}', 'alice', DAY), range(1, 20)))
    assert row(db)[0] == 1212
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: record_vote('r18', 'r19', 'bob', DAY), range(10)))
    assert sum(r['recorded'] for r in results) == 1


def test_opposite_concurrent_votes_are_zero_sum(db):
    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(lambda i: record_vote(*(('r0','r1') if i % 2 else ('r1','r0')), f'user-{i}', DAY), range(20)))
    assert row(db)[0] + row(db, 'r1')[0] == 2400
    assert row(db)[1] == 20


def test_failed_vote_rolls_back_pair_budget_counts_and_rating(db):
    with db.connection() as conn:
        conn.execute("""CREATE FUNCTION reject_test_update() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.role_id = 'r1' THEN RAISE EXCEPTION 'test failure'; END IF; RETURN NEW; END $$""")
        conn.execute('CREATE TRIGGER fail_vote BEFORE UPDATE ON idol_ratings FOR EACH ROW EXECUTE FUNCTION reject_test_update()')
    with pytest.raises(psycopg.Error):
        record_vote('r0', 'r1', 'alice', DAY)
    assert row(db) == (1200, 0)
    with db.connection() as conn:
        for table in ('visitor_pair_votes', 'visitor_idol_budget', 'sorter_ballots'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        conn.execute('DROP TRIGGER fail_vote ON idol_ratings')
    assert record_vote('r0', 'r1', 'alice', DAY)['recorded']


def test_ranked_limit_and_snapshots_use_same_eligibility(db):
    record_vote('r0', 'r1', 'user-0', DAY)
    # High-scoring but provisional idol must not displace eligible ranks.
    with db.connection() as conn:
        conn.execute("UPDATE idol_ratings SET global_elo=2000,global_match_count=0 WHERE role_id='r2'")
        conn.execute("UPDATE idol_ratings SET global_match_count=0 WHERE role_id='779828161319534595'")
    board = bias.get_global_leaderboard(2, include_provisional=False)
    assert [e.role_id for e in board.entries] == ['r0', 'r1']
    assert [e.rank for e in board.entries] == [1, 2]
    with db.connection() as conn:
        with conn.cursor() as cur:
            snapshots = bias._fetch_global_snapshot_rows(cur, 2)
    assert [(r[0], r[1], r[2]) for r in snapshots] == [(e.role_id, e.rank, e.elo) for e in board.entries]
    assert bias.create_weekly_global_snapshot(DAY)
    assert not bias.create_weekly_global_snapshot(DAY)
    assert not bias.create_weekly_global_snapshot(DAY + datetime.timedelta(days=7))
    assert bias.get_global_group_leaderboard().entries


def test_invalid_id_does_not_consume_budget(db):
    assert record_vote('r0', 'not-an-idol', 'alice', DAY)['reason'] == 'unknown_id'
    with db.connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM visitor_pair_votes').fetchone()[0] == 0


def test_win_cannot_lower_legacy_score(db):
    with db.connection() as conn:
        conn.execute("UPDATE idol_ratings SET global_elo=1100,global_match_count=15 WHERE role_id='r0'")
    before = row(db)[0]
    record_vote('r0', 'r1', 'alice', DAY)
    assert row(db)[0] > before
    board = bias.get_global_leaderboard(100, include_provisional=False)
    assert next(e for e in board.entries if e.role_id == 'r0').elo >= round(before)


def test_fresh_faces_have_their_own_limit(db):
    with db.connection() as conn:
        conn.execute("UPDATE idol_ratings SET global_match_count=1 WHERE role_id LIKE 'r%'")
    board = bias.get_global_leaderboard(3)
    assert len([e for e in board.entries if not e.provisional]) == 3
    assert len([e for e in board.entries if e.provisional]) == bias.FRESH_FACE_LIMIT
    assert len(bias.get_global_leaderboard(3, include_provisional=False).entries) == 3


def test_only_hashed_visitor_keys_are_written(db):
    record_vote('r0', 'r1', 'private-cookie', DAY)
    with db.connection() as conn:
        for table in ('visitor_pair_votes', 'visitor_idol_budget'):
            keys = conn.execute(f'SELECT DISTINCT visitor_token FROM {table}').fetchall()
            assert keys == [(bias.visitor_key('private-cookie'),)]


def test_catalog_check_detects_missing_sync(db):
    with db.connection() as conn:
        conn.execute("DELETE FROM idol_ratings WHERE role_id='sorter:0'")
        with pytest.raises(RuntimeError, match='catalog identities missing'):
            check_catalog_ratings(conn, CATALOG_ROWS)
        sync_catalog_ratings(conn, CATALOG_ROWS)
        check_catalog_ratings(conn, CATALOG_ROWS)


def test_simultaneous_snapshots_do_not_partial_fill(db):
    for i in range(1, 5):
        record_vote('r0', f'r{i}', f'user-{i}', DAY)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: bias.create_weekly_global_snapshot(DAY), range(2)))
    assert sorted(results) == [False, True]
    with db.connection() as conn:
        expected = len(bias.get_global_leaderboard(include_provisional=False).entries)
        assert conn.execute('SELECT COUNT(*) FROM idol_leaderboard_snapshots').fetchone()[0] == expected


def test_unexpected_snapshot_rank_collision_rolls_back(db):
    bad_rows = [('r0', 1, 1200, 1), ('r1', 1, 1200, 1)]
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.connection() as conn:
            with conn.cursor() as cur:
                bias._insert_snapshot_if_changed(cur, DAY, bad_rows)
    with db.connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM idol_leaderboard_snapshots').fetchone()[0] == 0


def test_shared_identity_metadata_is_independent_of_catalog_order():
    entries = json.loads((ROOT / 'static/sorter/catalog.json').read_text())['entries']
    forward = {row[0]: row[1:] for row in catalog_rating_rows(entries)}
    reverse = {row[0]: row[1:] for row in catalog_rating_rows(list(reversed(entries)))}
    assert forward == reverse
    canonical = next(entry for entry in entries if entry['id'] == 1027)
    assert forward['779826921613426708'] == ('Hyunjin', 'LOOSSEMBLE', canonical['local'] or canonical['photo'])
    # A future duplicate must be resolved deliberately, not silently first/last-wins.
    other = {'kind': 'idol', 'id': 9001, 'leaderboard_id': 'new-duplicate',
             'short': 'Example', 'group': 'Group', 'local': 'photo'}
    with pytest.raises(ValueError, match='canonical catalog entry'):
        catalog_rating_rows([other, {**other, 'id': 9002}])


def test_completed_ballot_includes_later_answers_and_ties(db):
    pairs = [('r0', 'r1', 1), ('r0', 'r2', 1), ('r0', 'r3', 0), ('r0', 'r4', 0), ('r0', 'r5', .5)]
    assert bias.record_sorter_ballot(str(uuid.uuid4()), pairs, 'alice', DAY)['recorded']
    assert row(db) == (1200, 5)
    assert row(db, 'r1')[0] < 1200 < row(db, 'r4')[0]


def test_ballot_receipt_survives_day_change_and_other_visitor(db):
    ballot = str(uuid.uuid4())
    pairs = [('r0', 'r1', 1)]
    assert bias.record_sorter_ballot(ballot, pairs, 'alice', DAY)['recorded']
    before = row(db)
    assert bias.record_sorter_ballot(ballot, pairs, 'bob', DAY + datetime.timedelta(days=1))['reason'] == 'repeat_ballot'
    assert row(db) == before


def test_concurrent_same_ballot_different_visitors_counts_once(db):
    ballot = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda i: bias.record_sorter_ballot(ballot, [('r0', 'r1', 1)], str(i), DAY), range(5)))
    assert sum(r['recorded'] for r in results) == 1
    assert row(db) == (1206, 1)


def test_ballot_last_answer_replaces_prior_answer(db):
    result = bias.record_sorter_ballot(str(uuid.uuid4()), [('r0', 'r1', 1), ('r1', 'r0', .5)], 'alice', DAY)
    assert result['comparisons'] == 1
    assert row(db) == (1200, 1)


def test_long_completed_ballot_is_normalized(db):
    bias.record_sorter_ballot(str(uuid.uuid4()), [('r0', f'r{i}', 1) for i in range(1, 20)], 'alice', DAY)
    assert row(db) == (1206, 19)
    with db.connection() as conn:
        assert conn.execute("SELECT SUM(global_elo) FROM idol_ratings WHERE role_id LIKE 'r%'").fetchone()[0] == 24000


def test_ranks_use_unrounded_scores_for_board_cutoff_and_snapshots(db):
    with db.connection() as conn:
        conn.execute("UPDATE idol_ratings SET global_match_count=0")
        conn.execute("UPDATE idol_ratings SET member_name='Alpha',global_elo=1300.1,global_match_count=1 WHERE role_id='r0'")
        conn.execute("UPDATE idol_ratings SET member_name='Zulu',global_elo=1300.4,global_match_count=1 WHERE role_id='r1'")
    board = bias.get_global_leaderboard(1, include_provisional=False)
    assert [(e.role_id, e.elo) for e in board.entries] == [('r1', 1300)]
    with db.connection() as conn:
        with conn.cursor() as cur:
            snapshot = bias._fetch_global_snapshot_rows(cur, 1)
    assert snapshot[0][:3] == ('r1', 1, 1300)


def test_group_ranks_use_unrounded_average(db, monkeypatch):
    monkeypatch.setattr(bias, '_group_memberships', lambda: [
        {'role_id': 'r0', 'group_name': 'Alpha'}, {'role_id': 'r1', 'group_name': 'Zulu'}])
    with db.connection() as conn:
        conn.execute("UPDATE role_info SET image_url=NULL")
        conn.execute("UPDATE role_info SET group_name='Alpha',image_url='photo' WHERE role_id='r0'")
        conn.execute("UPDATE role_info SET group_name='Zulu',image_url='photo' WHERE role_id='r1'")
        conn.execute("UPDATE idol_ratings SET global_elo=1300.1,global_match_count=50 WHERE role_id='r0'")
        conn.execute("UPDATE idol_ratings SET global_elo=1300.4,global_match_count=50 WHERE role_id='r1'")
    board = bias.get_global_group_leaderboard(1)
    assert [(e.group_name, e.elo) for e in board.entries] == [('Zulu', 1300)]


def test_group_board_uses_catalog_members_without_roles_and_shared_scores(db, monkeypatch):
    monkeypatch.setattr(bias, '_group_memberships', lambda: [
        {'role_id': 'sorter:0', 'group_name': 'First'},
        {'role_id': 'sorter:0', 'group_name': 'Second'},
        {'role_id': 'r0', 'group_name': 'First'},
    ])
    with db.connection() as conn:
        assert conn.execute("SELECT 1 FROM role_info WHERE role_id='sorter:0'").fetchone() is None
        conn.execute("UPDATE idol_ratings SET global_elo=1500, global_match_count=20 WHERE role_id='sorter:0'")
        conn.execute("UPDATE idol_ratings SET global_elo=1200, global_match_count=20 WHERE role_id='r0'")
    board = bias.get_global_group_leaderboard()
    groups = {e.group_name: e for e in board.entries}
    assert groups['First'].elo == 1350
    assert groups['First'].member_count == 2
    assert groups['Second'].elo == 1500
    assert groups['Second'].member_count == 1
    assert len(groups) == 2
