from src.content_dedup import MediaSet
from src.services.content_fingerprint import Fingerprinter


def fingerprint(bits):
    base = int('aa' * 32, 16)
    value = base ^ bits
    return {
        'version': 1,
        'duration': 2.0,
        'frames': [f'{value:064x}'] * 5,
        'colors': [100] * 15,
    }


def test_media_match_finds_partial_overlap_in_incomplete_stored_set():
    source = MediaSet('source', complete=False)
    source.add('role', 'source-one', content_id='one')
    source.add('role', 'source-two', content_id='two')
    source.add('role', 'source-unique', content_id='unique')
    post = MediaSet('discord-root')
    post.add('role', 'album')

    media = Fingerprinter(cache_writes=False, seconds=5)
    media.assets = lambda url: {'album': ('discord-one', 'discord-two', 'discord-other')}.get(url, (url,))
    fingerprints = {
        'source-one': fingerprint(0),
        'source-two': fingerprint(4),
        'source-unique': fingerprint((1 << 80) - 1),
        'discord-one': fingerprint(2),
        'discord-two': fingerprint(5),
        'discord-other': fingerprint((1 << 100) - 1),
    }
    media.fingerprint = lambda url: fingerprints[url]

    matches, complete = media.match_contents(source, [post])

    assert complete is True
    assert {(match['content_id'], match['discord_root_id']) for match in matches} == {
        ('one', 'discord-root'), ('two', 'discord-root'),
    }
    assert all(match['evidence']['kind'] == 'video-fingerprint-v1' for match in matches)


def test_member_matching_uses_injective_assignments():
    source = MediaSet('source', complete=False)
    source.add('role', 'source-one', content_id='one')
    source.add('role', 'source-two', content_id='two')
    post = MediaSet('discord-root')
    post.add('role', 'album')

    media = Fingerprinter(cache_writes=False, seconds=5)
    media.assets = lambda url: {'album': ('discord-one',)}.get(url, (url,))
    media.fingerprint = lambda _url: fingerprint(0)

    matches, complete = media.match_contents(source, [post])

    assert complete is True
    assert len(matches) == 1


def test_goyangi_viewer_assets_do_not_expand_through_metadata_api():
    media = Fingerprinter(cache_writes=False, seconds=5)
    viewer = 'https://goyangi.pics/v/260925-newjeans-danielle-d0150f1a.webp'
    assert media.assets(viewer) == (viewer,)
