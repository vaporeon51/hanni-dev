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
from src.content_dedup import media_key, full_media_match
from src.db import POOL

MAX_BYTES = 64 * 1024 * 1024


class Unverified(Exception):
    pass


class Fingerprinter:
    def __init__(self, *, cache_writes=True, seconds=45):
        self.cache_writes = cache_writes
        self.deadline = time.monotonic() + seconds
        self.session = requests.Session()
        self.rate_limited = False
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
        if self.rate_limited:
            raise Unverified('upstream rate limited; retry later')
        kwargs.setdefault('timeout', self.request_timeout())
        r = self.session.get(url, **kwargs)
        if r.status_code == 429:
            self.rate_limited = True
        r.raise_for_status()
        return r

    def assets(self, url):
        if url in self._assets_cache:
            return self._assets_cache[url]
        key = media_key(url)
        if key.startswith('imgur:album:'):
            client_id = os.getenv('IMGUR_CLIENT_ID', '').strip()
            if not client_id:
                raise Unverified('IMGUR_CLIENT_ID is required to expand albums')
            aid = key.rsplit(':', 1)[-1]
            with self.get(f'https://api.imgur.com/3/album/{aid}/images',
                          headers={'Authorization': 'Client-ID ' + client_id}) as r:
                data = r.json()['data']
            if not data:
                raise Unverified('empty album')
            urls = [x.get('mp4') or x.get('link') for x in data]
            if any(not x or urlsplit(x).hostname != 'i.imgur.com' for x in urls):
                raise Unverified('unsupported album asset')
            self._assets_cache[url] = tuple(urls)
            return self._assets_cache[url]
        if key.startswith('imgur:image:'):
            assets = ('https://i.imgur.com/' + key.rsplit(':', 1)[-1] + '.mp4',)
            self._assets_cache[url] = assets
            return assets
        if key.startswith('goyangi-cdn:'):
            assets = ('https://cdn.goyangi.pics' + key[len('goyangi-cdn:'):] + '.mp4',)
            self._assets_cache[url] = assets
            return assets
        if key.startswith('goyangi:'):
            cid = key[len('goyangi:'):]
            with self.get('https://api.goyangi.pics/api/collections/contents/records/' + cid) as r:
                original = r.json().get('original', '')
            if urlsplit(original).hostname != 'cdn.goyangi.pics':
                raise Unverified('unsupported Goyangi original')
            self._assets_cache[url] = (original,)
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

    def set_fingerprints(self, item):
        if not item.complete:
            raise Unverified('incomplete set metadata')
        assets = set()
        for url in sorted(item.urls):
            assets.update(self.assets(url))
            if len(assets) > 40:
                raise Unverified('set exceeds 40-clip verification limit')
        return [self.fingerprint(url) for url in sorted(assets)]

    def match(self, item, candidates):
        """Return match or None. Any incomplete comparison raises Unverified."""
        try:
            left = self.set_fingerprints(item)
            incomplete = False
            for post in candidates:
                try:
                    right = self.set_fingerprints(post)
                    if full_media_match(left, right):
                        return post, {'kind': 'video-fingerprint-v1', 'clips': len(left)}
                except (requests.RequestException, Unverified, ValueError, KeyError):
                    incomplete = True
                    if self.rate_limited:
                        break
            if incomplete:
                raise Unverified('some candidate media could not be verified')
            return None
        except (requests.RequestException, subprocess.SubprocessError, OSError, ValueError, KeyError) as e:
            raise Unverified(type(e).__name__) from e


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
