"""Cross-source set matching. Names/time only shortlist; media proves a match."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
import os
import re

IMGUR_HOSTS = {'imgur.com', 'www.imgur.com', 'm.imgur.com', 'i.imgur.com', 'i.imgur.gg'}
VISUAL_REVIEW_REASON = 'visual duplicate requires explicit auto-dedupe opt-in'


def media_key(url: str) -> str:
    """Normalize known host aliases, not title words or arbitrary suffix domains."""
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    parts = parsed.path.strip('/').split('/')
    if host in IMGUR_HOSTS:
        if len(parts) == 2 and parts[0] in {'a', 'gallery'}:
            ident = parts[1].split('.')[0].rsplit('-', 1)[-1]
            kind = 'album'
        elif len(parts) == 1:
            ident = parts[0].split('.')[0]
            kind = 'image'
        else:
            return url
        if re.fullmatch(r'[A-Za-z0-9]{5,10}', ident):
            return f'imgur:{kind}:{ident}'
    if host == 'goyangi.pics' and len(parts) == 2 and parts[0] == 'v':
        return 'goyangi:' + parts[1].split('.')[0]
    if host == 'cdn.goyangi.pics' and parsed.path.startswith('/v1/'):
        return 'goyangi-cdn:' + re.sub(r'(?:-sd|-static)?\.(?:webp|mp4|webm)$', '', parsed.path)
    return url


@dataclass
class MediaSet:
    id: str
    roles: set[str] = field(default_factory=set)
    urls: set[str] = field(default_factory=set)
    keys: set[str] = field(default_factory=set)
    members: dict[str, set[str]] = field(default_factory=dict)
    member_roles: dict[str, set[str]] = field(default_factory=dict)
    member_urls: dict[str, set[str]] = field(default_factory=dict)
    dates: list[datetime] = field(default_factory=list)
    # Set membership is authoritative only when built from a complete source
    # expansion. Database snapshots of Goyangi rows cannot establish that.
    complete: bool = True

    def add(self, role, url, created=None, content_id=None, original_url=None, mirror_url=None):
        self.roles.add(str(role))
        # Compare the complete source media, while retaining preview identities
        # for direct cross-host overlap checks.
        self.urls.add(original_url or url)
        self.keys.add(media_key(url))
        member = 'goyangi:' + content_id if content_id else media_key(url)
        self.members.setdefault(member, set()).update((media_key(url),))
        self.member_roles.setdefault(member, set()).add(str(role))
        self.member_urls.setdefault(member, set()).add(url)
        for alternate_url in (original_url, mirror_url):
            if alternate_url:
                identity = media_key(alternate_url)
                self.keys.add(identity)
                self.members[member].add(identity)
                self.member_urls[member].add(alternate_url)
        if content_id:
            self.keys.add('goyangi:' + content_id)
            self.members[member].add('goyangi:' + content_id)
        if created:
            self.dates.append(created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created)


class MediaSetIndex:
    """Inverted indexes make repeated per-set checks proportional to shared keys."""
    def __init__(self, posts):
        self.posts = posts.posts if isinstance(posts, MediaSetIndex) else posts
        self.by_key = {}
        self.by_role = {}
        for post in self.posts:
            for key in post.keys:
                self.by_key.setdefault(key, []).append(post)
            for role in post.roles:
                self.by_role.setdefault(role, []).append(post)

    def exact_match(self, goyangi):
        if not goyangi.complete:
            return None
        source_nodes = sorted(
            (member, role)
            for member, roles in goyangi.member_roles.items()
            for role in roles
        )
        # Exact set removal also needs an injective assignment. Without it, a
        # single Discord clip whose URL aliases two source members could count
        # twice and incorrectly suppress the rest of a Goyangi set.
        for post in sorted(self.posts, key=lambda item: item.id):
            target_nodes = sorted(
                (member, role)
                for member, roles in post.member_roles.items()
                for role in roles
            )
            edges = {}
            shared_by_edge = {}
            for source_index, (member, role) in enumerate(source_nodes):
                source_keys = goyangi.members.get(member, set())
                for target_index, (target_member, target_role) in enumerate(target_nodes):
                    if role != target_role:
                        continue
                    shared = source_keys & post.members.get(target_member, set())
                    if shared:
                        edges.setdefault(source_index, []).append(target_index)
                        shared_by_edge[(source_index, target_index)] = shared

            owner = {}

            def assign(source_index, seen):
                for target_index in edges.get(source_index, ()):
                    if target_index in seen:
                        continue
                    seen.add(target_index)
                    previous = owner.get(target_index)
                    if previous is None or assign(previous, seen):
                        owner[target_index] = source_index
                        return True
                return False

            if source_nodes and all(assign(index, set()) for index in range(len(source_nodes))):
                assignments = {source_index: target_index
                               for target_index, source_index in owner.items()}
                shared_keys = sorted({key for edge, keys in shared_by_edge.items()
                                      if assignments.get(edge[0]) == edge[1]
                                      for key in keys})
                return post, {'kind': 'shared-media', 'keys': shared_keys}
        return None

    def candidate_posts(self, goyangi):
        candidates = {}
        if goyangi.dates:
            for role in goyangi.roles:
                for post in self.by_role.get(role, ()):
                    if post.dates and abs(min(post.dates) - min(goyangi.dates)) <= timedelta(hours=24):
                        candidates[post.id] = post
        for key in goyangi.keys:
            for post in self.by_key.get(key, ()):
                if post.roles & goyangi.roles:
                    candidates[post.id] = post
        return [candidates[key] for key in sorted(candidates)]


def exact_match(goyangi: MediaSet, discord):
    """Require exact identity coverage of every Goyangi member in a post."""
    index = discord if isinstance(discord, MediaSetIndex) else MediaSetIndex(discord)
    return index.exact_match(goyangi)


def media_signature(media: MediaSet) -> tuple:
    """Stable signature for checking that both sides stayed unchanged."""
    return (
        media.complete,
        tuple(sorted(media.roles)),
        tuple(sorted(media.urls)),
        tuple(sorted(media.keys)),
        tuple(sorted((member, tuple(sorted(keys))) for member, keys in media.members.items())),
        tuple(sorted((member, tuple(sorted(roles))) for member, roles in media.member_roles.items())),
        tuple(sorted((member, tuple(sorted(urls))) for member, urls in media.member_urls.items())),
        tuple(sorted(date.isoformat() for date in media.dates)),
    )


def visual_auto_dedupe_enabled() -> bool:
    """Visual matches require an explicit opt-in until thresholds are calibrated."""
    return os.getenv('GOYANGI_ALLOW_VISUAL_AUTO_DEDUPE', '').strip().lower() in {
        '1', 'true', 'yes', 'on'
    }


def nearby_posts(goyangi: MediaSet, discord):
    """Return role/time candidate posts (indexed when possible)."""
    index = discord if isinstance(discord, MediaSetIndex) else MediaSetIndex(discord)
    if not goyangi.dates:
        return []
    return [post for role in goyangi.roles for post in index.by_role.get(role, ())
            if post.dates and abs(min(post.dates) - min(goyangi.dates)) <= timedelta(hours=24)]


def candidate_posts(goyangi: MediaSet, discord):
    """Temporal/role shortlist plus any partial exact-identity overlaps."""
    index = discord if isinstance(discord, MediaSetIndex) else MediaSetIndex(discord)
    return index.candidate_posts(goyangi)


def fingerprint_equal(a: dict, b: dict) -> bool:
    # Conservative: all frames must agree, as must duration. Never accept a
    # black/title frame as sufficient evidence. Version invalidates old caches.
    if a.get('version') != 1 or b.get('version') != 1:
        return False
    if abs(a['duration'] - b['duration']) > max(.15, .015 * max(a['duration'], b['duration'])):
        return False
    if len(a['frames']) != 5 or len(b['frames']) != 5:
        return False
    distances = [(int(x, 16) ^ int(y, 16)).bit_count() for x, y in zip(a['frames'], b['frames'])]
    # 256-bit dHash. Reject low-information frames; require comparable color
    # statistics as an independent check against similar silhouettes.
    # Calibrated 2026-09-28 on 8 user-verified cross-host dupes (worst max 13,
    # sum 45, all durations identical) vs 6 cross-idol negatives (best max 141,
    # sum 624): keeps ~1.5x headroom over positives, ~7x clear of negatives.
    return (all(24 <= int(x, 16).bit_count() <= 232 for x in a['frames'] + b['frames'])
            and max(distances) <= 20 and sum(distances) <= 60
            and all(abs(x - y) <= 10 for x, y in zip(a['colors'], b['colors']))
            and len(a['colors']) == len(b['colors']) == 15)


def full_media_match(left: list[dict], right: list[dict]) -> bool:
    """Injective matching: each Goyangi clip needs its own Discord counterpart."""
    if not left or len(left) > len(right):
        return False
    edges = [[j for j, other in enumerate(right) if fingerprint_equal(item, other)] for item in left]
    assigned = {}
    def augment(i, seen):
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in assigned or augment(assigned[j], seen):
                assigned[j] = i
                return True
        return False
    return all(augment(i, set()) for i in range(len(left)))
