import pytest

from src.goyangi_ingest import resolve_role


@pytest.mark.parametrize('stored_group', ['idle', 'gidle'])
@pytest.mark.parametrize('code', ['idle_yuqi', 'gidle_yuqi', 'i-dle_yuqi'])
def test_idle_rename_preserves_source_role_resolution(stored_group, code):
    assert resolve_role(code, {('yuqi', stored_group): 'yuqi-id'}, {stored_group}) == 'yuqi-id'
