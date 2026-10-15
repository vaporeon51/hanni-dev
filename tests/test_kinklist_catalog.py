"""Reference coverage and saved-answer compatibility, without network calls."""
import json
from collections import Counter
from pathlib import Path

from scripts import sync_kinklist_catalog as sync

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    return json.loads((ROOT / name).read_text())

def test_every_reference_entry_has_matching_local_rating_fields():
    catalog = load('static/kinklist/catalog.json')
    coverage = load('static/kinklist/reference-coverage.json')
    items = {item['id']: cat for cat in catalog['categories'] for item in cat['items']}
    assert len(items) == sum(len(cat['items']) for cat in catalog['categories'])
    assert Counter(entry['preset'] for entry in coverage['entries']) == {
        'classic': 118, 'detailed': 152, 'plsno': 351,
    }
    for entry in coverage['entries']:
        assert entry['targets'], entry
        for target in entry['targets']:
            assert target in items, entry
            assert items[target]['fields'] == entry['fields'], entry


def test_original_backup_keys_are_still_valid():
    catalog = load('static/kinklist/catalog.json')
    original = load('tests/fixtures/kinklist-original-backup.json')
    keys = {f"{item['id']}:{field}" for cat in catalog['categories'] for item in cat['items'] for field in cat['fields']}
    assert set(original['answers']) <= keys
    assert len({key.split(':')[0] for key in original['answers']}) == 90


def test_reference_sync_does_not_duplicate_or_replace_existing_items(tmp_path, monkeypatch):
    catalog = load('static/kinklist/catalog.json')
    coverage = load('static/kinklist/reference-coverage.json')
    destination = tmp_path / 'static/kinklist'
    destination.mkdir(parents=True)
    (destination / 'catalog.json').write_text(json.dumps(catalog))
    for preset in ['classic', 'detailed', 'plsno']:
        lines = []
        for entry in coverage['entries']:
            if entry['preset'] == preset:
                lines.extend([f"#{entry['category']}", '(' + ', '.join(entry['fields']) + ')', f"* {entry['label']}"])
        (tmp_path / f'{preset}.txt').write_text('\n'.join(lines))
    monkeypatch.setattr(sync, 'ROOT', tmp_path)
    sync.sync(tmp_path)
    assert json.loads((destination / 'catalog.json').read_text()) == catalog
