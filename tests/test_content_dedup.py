from datetime import datetime, timezone
from src.content_dedup import MediaSet, candidate_posts, exact_match, fingerprint_equal, full_media_match, media_key, nearby_posts


def test_host_aware_media_keys_ignore_imgur_slug_words_and_extensions():
    assert media_key('https://imgur.com/a/asa-x-babymonster-capo-IIKllKi') == 'imgur:album:IIKllKi'
    assert media_key('https://imgur.com/IIKllKi') == 'imgur:image:IIKllKi'
    assert media_key('https://goyangi.pics/v/260927-itzy-yuna-077a57e5.webp') == 'goyangi:260927-itzy-yuna-077a57e5'
    assert media_key('https://cdn.goyangi.pics/v1/itzy/yuna/x-sd.mp4') == media_key('https://cdn.goyangi.pics/v1/itzy/yuna/x.webp')
    assert media_key('https://imgur.com.bad.invalid/IIKllKi') != 'imgur:image:IIKllKi'


def test_exact_media_identity_matches_whole_set_and_returns_evidence():
    goyangi = MediaSet('g1')
    goyangi.add('r1', 'https://cdn.goyangi.pics/v1/a/yuna/clip.webp', content_id='clip')
    discord = MediaSet('d1')
    discord.add('r1', 'https://goyangi.pics/v/clip.webp')
    assert exact_match(goyangi, [discord]) == (discord, {'kind': 'shared-media', 'keys': ['goyangi:clip']})


def test_visual_match_requires_all_goyangi_clips_and_injective_matches():
    base = int('aa' * 32, 16)
    a = {'version': 1, 'duration': 2.0, 'frames': [f'{base:064x}'] * 5, 'colors': [100] * 15}
    b = {**a, 'frames': [f'{base ^ 3:064x}'] * 5}
    different = {**a, 'frames': ['0' * 64] * 5}
    assert fingerprint_equal(a, b)
    assert not fingerprint_equal(a, different)
    assert full_media_match([a], [b, different])
    assert not full_media_match([a, b], [a])
    assert not full_media_match([a, different], [b])


def test_nearby_shortlist_requires_shared_role_and_within_one_day():
    now = datetime.now(timezone.utc)
    source = MediaSet('g')
    source.add('r1', 'https://cdn.goyangi.pics/v1/a/b.webp', now)
    same = MediaSet('same')
    same.add('r1', 'https://imgur.com/x12345', now)
    wrong_role = MediaSet('wrong')
    wrong_role.add('r2', 'https://imgur.com/y12345', now)
    late = MediaSet('late')
    late.add('r1', 'https://imgur.com/z12345', now.replace(day=max(1, now.day-1)))
    assert nearby_posts(source, [same, wrong_role]) == [same]


def test_one_exact_member_is_only_a_candidate_when_a_set_has_more_members():
    goyangi = MediaSet('g')
    goyangi.add('r1', 'https://cdn.goyangi.pics/v1/a/b/one.webp', content_id='one')
    goyangi.add('r1', 'https://cdn.goyangi.pics/v1/a/b/two.webp', content_id='two')
    discord = MediaSet('d')
    discord.add('r1', 'https://goyangi.pics/v/one.webp')
    assert exact_match(goyangi, [discord]) is None
    assert candidate_posts(goyangi, [discord]) == [discord]


def test_exact_member_coverage_confirms_a_single_clip_set():
    goyangi = MediaSet('g')
    goyangi.add('r1', 'https://cdn.goyangi.pics/v1/a/b/one.webp', content_id='one')
    discord = MediaSet('d')
    discord.add('r1', 'https://goyangi.pics/v/one.webp')
    assert exact_match(goyangi, [discord])[0] is discord


def test_whole_set_exact_match_requires_distinct_discord_members():
    goyangi = MediaSet('g')
    goyangi.add('r1', 'https://imgur.com/same123', content_id='one')
    goyangi.add('r1', 'https://imgur.com/same123', content_id='two')
    discord = MediaSet('d')
    discord.add('r1', 'https://imgur.com/same123')

    assert exact_match(goyangi, [discord]) is None
