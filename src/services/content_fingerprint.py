"""Bounded, cached video verification for cross-host duplicates.

Never infer a whole album's contents from its OpenGraph cover. Rate limits and
unavailable decoders leave a candidate pending, not classified as unique.
"""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from psycopg.types.json import Jsonb
from src.content_dedup import media_key, fingerprint_equal
from src.db import POOL

MAX_BYTES = 64 * 1024 * 1024

# Shared in-process album cache so per-set Fingerprinter instances in the
# backfill do not re-fetch the same Imgur album. DB table imgur_album_cache
# (migration 45) is the persistent layer with a 7-day TTL; albums are
# effectively immutable.
_ALBUM_MEM_CACHE: dict[str, tuple[str, ...]] = {}

ALBUM_CACHE_TTL_SECONDS = 7 * 24 * 3600
RATE_LIMIT_COOLDOWN_SECONDS = 300


class Unverified(Exception):
    pass


def _album_cache_get(aid: str) -> tuple[str, ...] | None:
    hit = _ALBUM_MEM_CACHE.get(aid)
    if hit is not None:
        return hit
    try:
        with POOL.connection() as connection:
            row = connection.execute(
                '''SELECT assets FROM imgur_album_cache
                   WHERE album_id = %s
                     AND fetched_at > NOW() - make_interval(secs => %s)''',
                (aid, ALBUM_CACHE_TTL_SECONDS),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    payload = row[0]
    if isinstance(payload, (list, tuple)) and all(isinstance(x, str) for x in payload):
        return tuple(payload)
    return None


def _album_cache_put(aid: str, assets: tuple[str, ...]) -> None:
    _ALBUM_MEM_CACHE[aid] = assets
    try:
        with POOL.connection() as connection:
            connection.execute(
                '''INSERT INTO imgur_album_cache(album_id, assets)
                   VALUES (%s, %s)
                   ON CONFLICT (album_id) DO UPDATE
                   SET assets = EXCLUDED.assets, fetched_at = NOW()''',
                (aid, Jsonb(list(assets))),
            )
    except Exception:
        pass


class Fingerprinter:
    def __init__(self, *, cache_writes=True, seconds=45):
        self.cache_writes = cache_writes
        self.deadline = time.monotonic() + seconds
        self.session = requests.Session()
        self.rate_limited_until = 0.0
        self._assets_cache = {}

    def check_budget(self):
        if time.monotonic() >= self.deadline:
            raise Unverified('verification time budget exhausted')

    def request_timeout(self):
        self.check_budget()
        remaining = self.deadline - time.monotonic()
        return min(3, remaining), min(5, remaining)

    def get(self, url, **kwargs):
        self.check_budget()
        if time.monotonic() < self.rate_limited_until:
            raise Unverified('upstream rate limited; retry later')
        kwargs.setdefault('timeout', self.request_timeout())
        r = self.session.get(url, **kwargs)
        if r.status_code == 429:
            self.rate_limited_until = time.monotonic() + RATE_LIMIT_COOLDOWN_SECONDS
        r.raise_for_status()
        return r

    def assets(self, url):
        if url in self._assets_cache:
            return self._assets_cache[url]
        key = media_key(url)
        if key.startswith('imgur:album:'):
            aid = key.rsplit(':', 1)[-1]
            cached = _album_cache_get(aid)
            if cached is not None:
                self._assets_cache[url] = cached
                return cached
            client_id = os.getenv('IMGUR_CLIENT_ID', '').strip()
            if not client_id:
                raise Unverified('IMGUR_CLIENT_ID is required to expand albums')
            with self.get(f'https://api.imgur.com/3/album/{aid}/images',
                          headers={'Authorization': 'Client-ID ' + client_id}) as r:
                data = r.json()['data']
            if not data:
                raise Unverified('empty album')
            urls = [x.get('mp4') or x.get('link') for x in data]
            if any(not x or urlsplit(x).hostname != 'i.imgur.com' for x in urls):
                raise Unverified('unsupported album asset')
            assets = tuple(urls)
            self._assets_cache[url] = assets
            if self.cache_writes:
                _album_cache_put(aid, assets)
            else:
                _ALBUM_MEM_CACHE[aid] = assets
            return assets
        if key.startswith('imgur:image:'):
            assets = ('https://i.imgur.com/' + key.rsplit(':', 1)[-1] + '.mp4',)
            self._assets_cache[url] = assets
            return assets
        if key.startswith('goyangi-cdn:'):
            assets = ('https://cdn.goyangi.pics' + key[len('goyangi-cdn:'):] + '.mp4',)
            self._assets_cache[url] = assets
            return assets
        if key.startswith('goyangi:'):
            # The stored viewer link is itself downloadable. Avoid fetching
            # Goyangi record metadata during matching and backfill.
            self._assets_cache[url] = (url,)
            return self._assets_cache[url]
        raise Unverified('unsupported media host')

    def fingerprint(self, url):
        with POOL.connection() as connection:
            row = connection.execute('SELECT fingerprint FROM content_fingerprints WHERE url=%s', (url,)).fetchone()
        if row and row[0].get('version') == 1:
            return row[0]
        self.check_budget()
        with tempfile.TemporaryDirectory(prefix='hanni-fingerprint-') as folder:
            path = Path(folder) / 'clip'
            with self.get(url, stream=True) as r, path.open('wb') as f:
                size = 0
                for chunk in r.iter_content(65536):
                    self.check_budget()
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise Unverified('media exceeds fingerprint download limit')
                    f.write(chunk)
            result = fingerprint_file(path, deadline=self.deadline)
        if not self.cache_writes:
            return result
        with POOL.connection() as connection:
            connection.execute('''INSERT INTO content_fingerprints(url,fingerprint) VALUES(%s,%s)
                                  ON CONFLICT(url) DO UPDATE SET fingerprint=EXCLUDED.fingerprint,
                                    created_at=NOW()''', (url, Jsonb(result)))
        return result

    def match_contents(self, item, candidates, *, visual=True, excluded=frozenset()):
        """Match individual source clips to distinct Discord media members.

        A positive match is useful even when the source set only partly
        overlaps a Discord set. ``complete`` is false if any asset could not
        be inspected, so callers can defer unmatched clips safely.
        """
        source_nodes = sorted(
            (member, role)
            for member, roles in item.member_roles.items()
            for role in roles
            if not (member.startswith('goyangi:')
                    and (member[len('goyangi:'):], role) in excluded)
        )
        exact_targets = []
        for post in candidates:
            for member, roles in post.member_roles.items():
                for role in roles:
                    exact_targets.append((post, member, role))
        exact_targets.sort(key=lambda target: (target[0].id, target[1], target[2]))

        if not source_nodes or not exact_targets:
            return [], bool(not source_nodes or not candidates)

        matches = {}
        exact_used_targets = set()
        exact_edges = {}
        exact_evidence = {}
        for source_index, (source_member, role) in enumerate(source_nodes):
            for target_index, (post, target_member, target_role) in enumerate(exact_targets):
                if role != target_role:
                    continue
                shared = item.members.get(source_member, set()) & post.members.get(target_member, set())
                if shared:
                    exact_edges.setdefault(source_index, []).append(target_index)
                    exact_evidence[(source_index, target_index)] = sorted(shared)

        def assign(edges, unavailable_targets=frozenset()):
            owner = {}

            def visit(source_index, seen):
                for target_index in sorted(edges.get(source_index, ())):
                    if target_index in seen or target_index in unavailable_targets:
                        continue
                    seen.add(target_index)
                    previous = owner.get(target_index)
                    if previous is None or visit(previous, seen):
                        owner[target_index] = source_index
                        return True
                return False

            for source_index in sorted(edges):
                visit(source_index, set())
            return {source_index: target_index for target_index, source_index in owner.items()}

        for source_index, target_index in assign(exact_edges).items():
            source_member, role = source_nodes[source_index]
            post, target_member, _ = exact_targets[target_index]
            matches[(source_member, role)] = {
                'member': source_member,
                'content_id': source_member[len('goyangi:'):] if source_member.startswith('goyangi:') else None,
                'role_id': role,
                'discord_root_id': post.id,
                'evidence': {'kind': 'shared-media', 'keys': exact_evidence[(source_index, target_index)]},
            }
            exact_used_targets.add((post.id, target_member, role))

        unmatched = [i for i, node in enumerate(source_nodes) if node not in matches]
        if not visual or not unmatched:
            return list(matches.values()), len(matches) == len(source_nodes)

        complete = True
        fingerprint_cache = {}

        def fingerprints(urls, seen_assets):
            nonlocal complete
            found = {}
            for media_url in sorted(urls):
                try:
                    asset_urls = self.assets(media_url)
                except (requests.RequestException, Unverified, ValueError, KeyError):
                    complete = False
                    continue
                for asset_url in asset_urls:
                    if asset_url not in seen_assets and len(seen_assets) >= 40:
                        complete = False
                        continue
                    seen_assets.add(asset_url)
                    if asset_url not in fingerprint_cache:
                        try:
                            fingerprint_cache[asset_url] = self.fingerprint(asset_url)
                        except (requests.RequestException, Unverified, subprocess.SubprocessError,
                                OSError, ValueError, KeyError):
                            complete = False
                            fingerprint_cache[asset_url] = None
                    result = fingerprint_cache[asset_url]
                    if result is not None:
                        found[asset_url] = result
            return found

        source_fingerprints = {}
        source_assets = set()
        for source_index in unmatched:
            member, _ = source_nodes[source_index]
            values = fingerprints(item.member_urls.get(member, set()), source_assets)
            source_fingerprints[source_index] = values
            if not values:
                complete = False

        target_slots = []
        target_assets = {}
        for post in candidates:
            seen_assets = set()
            for member, roles in post.member_roles.items():
                for role in roles:
                    if (post.id, member, role) in exact_used_targets:
                        continue
                    asset_fingerprints = target_assets.get((post.id, member))
                    if asset_fingerprints is None:
                        asset_fingerprints = fingerprints(post.member_urls.get(member, set()), seen_assets)
                        target_assets[(post.id, member)] = asset_fingerprints
                    if not asset_fingerprints:
                        complete = False
                    for asset_url, asset_fp in asset_fingerprints.items():
                        target_slots.append((post, member, role, asset_url, asset_fp))

        visual_edges = {}
        edge_evidence = {}
        for source_index in unmatched:
            source_member, role = source_nodes[source_index]
            for target_index, (post, target_member, target_role, target_url, target_fp) in enumerate(target_slots):
                if role != target_role:
                    continue
                source_values = source_fingerprints.get(source_index, {})
                evidence = next((
                    source_url
                    for source_url, source_fp in source_values.items()
                    if fingerprint_equal(source_fp, target_fp)
                ), None)
                if evidence:
                    visual_edges.setdefault(source_index, []).append(target_index)
                    edge_evidence[(source_index, target_index)] = (evidence, target_url)

        for source_index, target_index in assign(visual_edges).items():
            source_member, role = source_nodes[source_index]
            post, target_member, _, _, _ = target_slots[target_index]
            matched_source_url, matched_target_url = edge_evidence[(source_index, target_index)]
            matches[(source_member, role)] = {
                'member': source_member,
                'content_id': source_member[len('goyangi:'):] if source_member.startswith('goyangi:') else None,
                'role_id': role,
                'discord_root_id': post.id,
                'evidence': {
                    'kind': 'video-fingerprint-v1',
                    'source_asset': matched_source_url,
                    'discord_asset': matched_target_url,
                },
            }

        return list(matches.values()), complete


def fingerprint_file(path, *, deadline=None):
    def run(command):
        timeout = 20
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Unverified('verification time budget exhausted')
            timeout = min(timeout, remaining)
        result = subprocess.check_output(command, timeout=timeout)
        if deadline is not None and time.monotonic() >= deadline:
            raise Unverified('verification time budget exhausted')
        return result

    info = json.loads(run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(path)]))
    duration = float(info['format']['duration'])
    if not .3 <= duration <= 180:
        raise Unverified('unsupported clip duration')
    frames, colors = [], []
    for fraction in [.1, .3, .5, .7, .9]:
        frame = run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-ss', str(duration * fraction),
             '-i', str(path), '-frames:v', '1', '-vf', 'scale=17:16,format=rgb24', '-f', 'rawvideo', '-'])
        if len(frame) != 17 * 16 * 3:
            raise Unverified('incomplete decoded frame')
        gray = [sum(frame[i:i+3]) for i in range(0, len(frame), 3)]
        bits = sum((gray[y*17+x] > gray[y*17+x+1]) << (y*16+x) for y in range(16) for x in range(16))
        frames.append(f'{bits:064x}')
        colors.extend(round(sum(frame[channel::3]) / (17*16), 2) for channel in range(3))
    return {'version': 1, 'duration': duration, 'frames': frames, 'colors': colors}
