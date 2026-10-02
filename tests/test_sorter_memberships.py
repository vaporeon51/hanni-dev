"""Filter and leaderboard membership regressions, independent of live Postgres."""
import copy
import json
from pathlib import Path

from src.sorter_catalog import (
    catalog_group_memberships, catalog_rating_rows, normalize_catalog_memberships,
)
from src.db import bias

ROOT = Path(__file__).resolve().parents[1]


def catalog():
    return json.loads((ROOT / 'static/sorter/catalog.json').read_text())


def test_all_people_are_reachable_and_normalization_is_idempotent():
    data = catalog()
    before = copy.deepcopy(data)
    normalize_catalog_memberships(data['entries'], data['groups'])
    assert data == before
    keys = {g['key'] for g in data['groups']}
    assert all(set(e['groups']) <= keys for e in data['entries'] if e['kind'] == 'idol')
    selectable = [e for e in data['entries'] if e['kind'] == 'idol' and not e.get('canonical_id')]
    assert len({e['leaderboard_id'] for e in selectable}) == len(selectable)


def test_repairs_keep_saved_ids_and_identity_and_restore_unicode():
    data = catalog()
    entry = next(e for e in data['entries'] if e['id'] == 77)
    entry['name'] = r'Dal\\u2605Shabet Jiyul'
    entry['groups'] = [r'Dal\\u2605Shabet']
    entry['group'] = r'Dal\\u2605Shabet'
    before = [(e['id'], e.get('leaderboard_id')) for e in data['entries']]
    normalize_catalog_memberships(data['entries'], data['groups'])
    assert entry['name'] == 'Dal★Shabet Jiyul'
    assert entry['groups'] == ['Dal★Shabet']
    assert before == [(e['id'], e.get('leaderboard_id')) for e in data['entries']]


def test_group_memberships_share_one_score_and_exclude_solo_filters():
    data = catalog()
    memberships = catalog_group_memberships(data['entries'], data['groups'])
    pairs = [(m['role_id'], m['group_name']) for m in memberships]
    assert len(set(pairs)) == len(pairs)
    assert ('779830005642821634', 'IZ*ONE') in pairs
    assert ('779830005642821634', 'Hyewon') not in pairs
    assert sum(name == 'IZ*ONE' for _, name in pairs) == 3
    assert sum(name == 'fromis_9' for _, name in pairs) == 9
    assert ('779847756138807298', 'fromis_9') in pairs
    assert ('779826921613426708', 'LATENCY') in pairs
    assert ('779826921613426708', 'LOOSSEMBLE') in pairs
    without_roles = [e for e in data['entries'] if e['kind'] == 'idol' and not e.get('role_id')
                     and any(g['key'] in e['groups'] and g.get('gen') for g in data['groups'])]
    assert without_roles
    assert all(any(m['role_id'] == e['leaderboard_id'] for m in memberships) for e in without_roles)
    rows = catalog_rating_rows(data['entries'])
    assert len(rows) == len({e['leaderboard_id'] for e in data['entries'] if e['kind'] == 'idol'})


def test_group_query_uses_catalog_memberships_instead_of_role_info(monkeypatch):
    calls = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None): calls.append((sql, params))
        def fetchone(self): return (12,)
        def fetchall(self): return []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    class Pool:
        def connection(self): return Connection()
    monkeypatch.setattr(bias, '_pool', lambda: Pool())
    memberships = [{'role_id': 'sorter:0', 'group_name': 'Example'}]
    monkeypatch.setattr(bias, '_group_memberships', lambda: memberships)
    assert bias.get_global_group_leaderboard().vote_count == 12
    sql, params = calls[-1]
    assert 'role_info' not in sql
    assert 'JOIN idol_ratings' in sql
    assert json.loads(params[0]) == memberships
    assert params[1:] == (3, 3, 3, 3, 15)


def test_group_score_memberships_align_with_discovery_after_rebuild():
    data = catalog()
    by_name = {e['name']: e for e in data['entries'] if e['kind'] == 'idol'}
    assert by_name['Hyewon']['groups'] == ['IZ*ONE']
    assert by_name['Hyewon']['group'] == 'IZ*ONE'
    assert all(g['key'] != 'Hyewon' for g in data['groups'])
    assert by_name['Seoyeon (Y:SY)']['groups'] == ['Seoyeon', 'fromis_9']
    assert by_name['LE SSERAFIM Sakura']['groups'] == ['LE SSERAFIM']
    assert by_name['Kwon Eunbi']['groups'] == ['Kwon Eunbi']
    assert by_name['Jo Yuri']['groups'] == ['Jo Yuri']
    assert len([e for e in by_name.values() if 'IZ*ONE' in e['groups']]) == 3
    scores_before = catalog_group_memberships(data['entries'], data['groups'])
    # Previously shipped broad memberships are corrected on a rebuild too.
    by_name['LE SSERAFIM Sakura']['groups'].append('IZ*ONE')
    by_name['LE SSERAFIM Sakura']['leaderboard_groups'] = ['LE SSERAFIM', 'IZ*ONE']
    by_name['Hyewon']['groups'] = ['Hyewon', 'IZ*ONE']
    data['groups'].append({'name': 'Hyewon', 'key': 'Hyewon', 'gen': []})
    normalize_catalog_memberships(data['entries'], data['groups'])
    assert by_name['LE SSERAFIM Sakura']['groups'] == ['LE SSERAFIM']
    assert by_name['Hyewon']['groups'] == ['IZ*ONE']
    assert scores_before == catalog_group_memberships(data['entries'], data['groups'])
    assert all('leaderboard_groups' not in e for e in data['entries'])


def test_each_group_score_uses_exactly_its_selectable_filter_members():
    data = catalog()
    memberships = catalog_group_memberships(data['entries'], data['groups'])
    for group in data['groups']:
        if not group.get('gen'):
            assert not any(m['group_name'] == group['name'] for m in memberships)
            continue
        expected = {e['leaderboard_id'] for e in data['entries']
                    if e['kind'] == 'idol' and not e.get('canonical_id') and group['key'] in e['groups']}
        actual = {m['role_id'] for m in memberships if m['group_name'] == group['name']}
        assert actual == expected
    by_name = {e['name']: e for e in data['entries'] if e['kind'] == 'idol'}
    for name in ('Kwon Eunbi', 'Jo Yuri'):
        assert not any(m['role_id'] == by_name[name]['leaderboard_id'] for m in memberships)
    sakura_groups = {m['group_name'] for m in memberships
                     if m['role_id'] == by_name['LE SSERAFIM Sakura']['leaderboard_id']}
    assert sakura_groups == {'LE SSERAFIM'}


def test_seoyeon_display_rename_preserves_identity_and_search_aliases():
    data = catalog()
    entry = next(e for e in data['entries'] if e['id'] == 565)
    identity = entry['leaderboard_id']
    entry['name'] = 'Y:SY (Lee Seoyeon)'
    entry['short'] = 'Y:SY (Lee Seoyeon)'
    normalize_catalog_memberships(data['entries'], data['groups'])
    assert entry['name'] == entry['short'] == 'Seoyeon (Y:SY)'
    assert entry['leaderboard_id'] == identity == '779847756138807298'
    assert entry['search_aliases'] == ['Lee Seoyeon', 'Y:SY (Lee Seoyeon)']
    assert next(g for g in data['groups'] if g['key'] == 'Seoyeon')['name'] == 'Seoyeon (Y:SY)'
