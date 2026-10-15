"""Merge reference label/role inventories, retaining every existing catalog ID.

Pass --source-dir for downloaded classic.txt, detailed.txt and plsno.txt files;
otherwise fetch the public upstream presets. Descriptions are not copied.
The coverage ledger records every source entry and its local equivalent(s).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://raw.githubusercontent.com/adhesivecheese/kinklist/master/{}.txt'

def norm(text):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', text).lower())

# Equivalents only merge when the entire role schema also matches.
ALIASES = {
    'clothedsex': ['Staying dressed'], 'uniformcostume': ['Uniforms', 'Costumes'],
    'collar': ['Collars'], 'leash': ['Leashes'], 'handcuffs': ['Cuffs'],
    'gags': ['Gag'], 'generaldominantsubmissive': ['General Dom / General Sub'],
    'handjobfingering': ['Manual stimulation'], 'deepthroat': ['Deep throating'],
    'sensationplay': ['Sensory play'], 'hotwax': ['Wax play'], 'ice': ['Ice play'],
    'socksstockings': ['Stockings'], 'salivaspit': ['Saliva'],
    'photographyvideotaping': ['Photography / videotaping'],
}
# Same field schema and subject as an existing chapter.
BUCKETS = {
    'clothing': 'style', 'toys': 'toys', 'general': 'intimacy',
    'oralsex': 'oral', 'assplay': 'anal', 'restrictive': 'restriction',
    'bodies': 'bodies', 'bodypartsize': 'body-size',
    'rolesgroupings': 'pairings', 'pairings': 'pairings', 'groupings': 'groupings',
    'domination': 'power', 'noconsent': 'fiction-boundaries', 'taboo': 'taboo',
    'surrealism': 'surreal', 'generalsurrealism': 'surreal', 'voreunbirth': 'surreal-extreme',
    'fluids': 'fluids', 'degradation': 'degradation', 'touchstimulation': 'attention',
    'miscfetish': 'more-intimacy', 'pain': 'pain', 'cumrelated': 'semen',
    'bdsmrelated': 'bdsm', 'watersportsscat': 'fluids-extreme',
    'bloodgoretorturedeath': 'fiction-extreme', 'meta': 'writing',
    'medium': 'platforms', 'timescale': 'pacing',
}
TITLES = {
    'oral': 'Oral preferences', 'anal': 'Anal preferences', 'restriction': 'Restraint & accessories',
    'bodies': 'Bodies & appearance', 'body-size': 'Body proportions',
    'pairings': 'Roles & pairings', 'groupings': 'Partner combinations',
    'power': 'Power dynamics', 'fiction-boundaries': 'Fiction & consent boundaries',
    'taboo': 'Taboo fiction', 'surreal': 'Surreal fantasies', 'surreal-extreme': 'Vore & unbirth fiction',
    'fluids': 'Fluids', 'degradation': 'Degradation', 'attention': 'Focused attention',
    'more-intimacy': 'More intimate preferences', 'pain': 'Intensity & pain',
    'semen': 'Semen-related preferences', 'bdsm': 'More power & restraint',
    'fluids-extreme': 'More fluid preferences', 'fiction-extreme': 'Extreme fiction boundaries',
    'writing': 'Writing style', 'platforms': 'Where to connect', 'pacing': 'Scenes & reply pace',
}
DISPLAY = {
    'ddlgmdlb': 'Caregiver / little dynamic (adults)',
    'agegap': 'Age gap (adults)', 'asmtftrans': 'As a trans woman',
    'asftmtrans': 'As a trans man', 'youandmtftrans': 'You and a trans woman',
    'youandftmtrans': 'You and a trans man',
}

def parse(text):
    category, fields = None, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#'):
            category, fields = line[1:].strip(), None
        elif line.startswith('(') and line.endswith(')'):
            fields = [field.strip().replace('Recieving', 'Receiving').replace('As Dominant', 'Dominant').replace('As Submissive', 'Submissive') for field in line[1:-1].split(',')]
        elif line.startswith('*'):
            if not category or not fields:
                raise ValueError('Item without category or role fields')
            yield category, fields, line[1:].strip()

def sync(source_dir=None):
    path = ROOT / 'static/kinklist/catalog.json'
    catalog = json.loads(path.read_text())
    categories = {cat['id']: cat for cat in catalog['categories']}
    lookup = {}
    for cat in catalog['categories']:
        for item in cat['items']:
            lookup[(norm(item['name']), tuple(cat['fields']))] = item['id']
    ledger = {'sources': [], 'entries': []}
    for preset in ['classic', 'detailed', 'plsno']:
        url = SOURCE.format(preset)
        content = (source_dir / f'{preset}.txt').read_bytes() if source_dir else urlopen(url, timeout=30).read()
        ledger['sources'].append({'preset': preset, 'url': url, 'sha256': hashlib.sha256(content).hexdigest()})
        for category, fields, label in parse(content.decode('utf-8')):
            key = (norm(label), tuple(fields))
            equivalents = ALIASES.get(norm(label), [DISPLAY.get(norm(label), label)])
            targets = [lookup.get((norm(name), tuple(fields))) for name in equivalents]
            if not all(targets):
                if key in lookup:
                    targets = [lookup[key]]
                else:
                    bucket = BUCKETS[norm(category)]
                    cat_id = bucket
                    if cat_id in categories and categories[cat_id]['fields'] != fields:
                        cat_id += '-' + hashlib.sha256('|'.join(fields).encode()).hexdigest()[:6]
                    if cat_id not in categories:
                        title = TITLES.get(bucket, category)
                        categories[cat_id] = {'id': cat_id, 'name': title,
                            'description': 'Your preferences and boundaries, one detail at a time.' if bucket not in {'fiction-boundaries', 'taboo', 'fiction-extreme'} else 'Fictional adult themes and boundaries.',
                            'icon': 'sparkle', 'fields': fields, 'items': []}
                        catalog['categories'].append(categories[cat_id])
                    item_id = 'ref-' + hashlib.sha256((norm(label) + '|' + '|'.join(fields)).encode()).hexdigest()[:12]
                    name = DISPLAY.get(norm(label), equivalents[0])
                    categories[cat_id]['items'].append({'id': item_id, 'name': name})
                    lookup[(norm(name), tuple(fields))] = item_id
                    targets = [item_id]
            lookup[key] = targets[0]
            ledger['entries'].append({'preset': preset, 'category': category, 'label': label, 'fields': fields, 'targets': targets})
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    (path.parent / 'reference-coverage.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
    print(f"{sum(len(cat['items']) for cat in catalog['categories'])} preferences; {len(catalog['categories'])} categories; {len(ledger['entries'])} source entries covered")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path)
    sync(parser.parse_args().source_dir)
