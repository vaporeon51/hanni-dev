import asyncio
from unittest.mock import MagicMock

import httpx
import pytest

from src.db import disambiguation as db
from src.web import app as web
from src.web import disambiguation as routes


def request(method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url='http://test', cookies=kwargs.pop('cookies', None)) as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def test_unlisted_page_opens_without_password(monkeypatch):
    monkeypatch.delenv('DISAMBIGUATION_ADMIN_PASSWORD', raising=False)
    page = request('GET', '/disambiguation')
    assert page.status_code == 200
    assert 'id="queue"' in page.text
    assert 'id="login"' not in page.text
    assert 'noindex' in page.text
    assert '/static/media-player.js' in page.text
    monkeypatch.setattr(routes, 'list_review_sets', lambda: [])
    assert request('GET', '/api/disambiguation').json() == {'sets': []}


def test_save_requires_header_and_passes_multiple_labels(monkeypatch):
    save = MagicMock(return_value=7)
    monkeypatch.setattr(routes, 'save_review', save)
    args = dict(json={'selected': ['a', 'b'], 'expected': ['a', 'b', 'c']})
    assert request('POST', '/api/disambiguation/1', **args).status_code == 403
    assert request('POST', '/api/disambiguation/1', headers={'X-Admin-Review': '1', 'Sec-Fetch-Site': 'cross-site'}, **args).status_code == 403
    save.assert_not_called()
    response = request('POST', '/api/disambiguation/1', headers={'X-Admin-Review': '1'}, **args)
    assert response.json() == {'updated': 7}
    save.assert_called_once_with(1, ['a', 'b'], ['a', 'b', 'c'])


def test_clean_host_blocks_admin_content(monkeypatch):
    monkeypatch.setattr(web, 'CLEAN_HOST', 'clean.test')
    assert request('GET', '/api/disambiguation', headers={'Host': 'clean.test'}).status_code == 404


@pytest.mark.parametrize('selected,expected,error', [(['unknown'], ['a', 'b'], ValueError), (['a'], ['a'], LookupError)])
def test_save_rejects_unknown_or_stale_labels_without_update(monkeypatch, selected, expected, error):
    pool = MagicMock()
    cur = pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = ('https://example.test/image.webp',)
    cur.fetchall.return_value = [(1, 'a'), (2, 'b')]
    monkeypatch.setattr(db, 'POOL', pool)
    with pytest.raises(error):
        db.save_review(1, selected, expected)
    assert not any(call.args[0].lstrip().startswith('UPDATE') for call in cur.execute.call_args_list)


def test_save_retains_rows_and_preserves_dead_link_reports(monkeypatch):
    pool = MagicMock()
    cur = pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = ('https://example.test/image.webp',)
    cur.fetchall.return_value = [(1, 'a'), (2, 'b'), (3, 'c')]
    cur.rowcount = 3
    monkeypatch.setattr(db, 'POOL', pool)
    assert db.save_review(1, ['a', 'c'], ['a', 'b', 'c']) == 3
    query, params = cur.execute.call_args.args
    assert params[0] == ['a', 'c']
    assert 'disambiguated = TRUE' in query
    assert 'dead_link_reports' not in query
    assert 'DELETE' not in query


def test_admin_preview_loads_reported_image_without_public_collection_filter(monkeypatch):
    from src.services.media import ResolvedMedia
    monkeypatch.setattr(routes, 'get_live_content_url', lambda lid: 'https://i.imgur.com/example.jpg')
    monkeypatch.setattr(routes, 'resolve_media_url_cached', lambda url: ResolvedMedia('image', url))
    monkeypatch.setattr(web, 'load_collection_preview', MagicMock(side_effect=AssertionError('Public feed filter must not run')))
    result = request('GET', '/api/disambiguation/1/media')
    assert result.json() == {'kind': 'image', 'url': '/api/disambiguation/1/asset'}
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.headers = {'Content-Type': 'image/jpeg'}
    upstream.iter_content.return_value = [b'image bytes']
    monkeypatch.setattr(routes, 'open_media_stream', lambda url, range_header=None: upstream)
    asset = request('GET', result.json()['url'])
    assert asset.content == b'image bytes'
    assert asset.headers['cache-control'] == 'private, max-age=300'
    upstream.close.assert_called_once()


def test_broken_action_is_protected_and_marks_all_url_assignments(monkeypatch):
    pool = MagicMock()
    cur = pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = ('https://imgur.com/broken',)
    cur.rowcount = 7
    monkeypatch.setattr(db, 'POOL', pool)
    assert request('POST', '/api/disambiguation/1/broken').status_code == 403
    result = request('POST', '/api/disambiguation/1/broken', headers={'X-Admin-Review': '1'})
    assert result.json() == {'updated': 7}
    query, params = cur.execute.call_args.args
    assert 'is_dead = TRUE' in query
    assert 'WHERE url = %s' in query
    assert 'is_recovery_exhausted' in query
    assert params[1] == 'https://imgur.com/broken'


def test_set_returns_direct_preview_without_metadata_lookup(monkeypatch):
    pool = MagicMock()
    cur = pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = ('goyangi:set-1',)
    url = 'https://cdn.goyangi.pics/v1/group/photo.webp'
    cur.fetchall.return_value = [(1, url, 'a', 'Carmen', 'Hearts2Hearts', False, 1),
                                (2, url, 'b', 'Ian', 'Hearts2Hearts', False, 0)]
    monkeypatch.setattr(db, 'POOL', pool)
    result = db.get_review_set(1)
    assert len(result['items']) == 1
    assert result['items'][0]['preview'] == {'kind': 'image', 'url': url}
    assert result['items'][0]['roles'][0]['name'] == 'Carmen'


@pytest.mark.parametrize('url', ['https://imgur.com/a/album', 'https://untrusted.test/photo.jpg', 'http://i.imgur.com/photo.jpg'])
def test_non_direct_and_untrusted_urls_use_resolver(url):
    assert db.direct_preview(url) is None


def test_review_video_proxy_preserves_range_requests(monkeypatch):
    from src.services.media import ResolvedMedia
    monkeypatch.setattr(routes, 'get_live_content_url', lambda lid: 'https://i.imgur.com/video.mp4')
    monkeypatch.setattr(routes, 'resolve_media_url_cached', lambda url: ResolvedMedia('video', url))
    upstream = MagicMock()
    upstream.status_code = 206
    upstream.headers = {'Content-Type': 'video/mp4', 'Content-Range': 'bytes 0-3/100',
                        'Content-Length': '4', 'Accept-Ranges': 'bytes'}
    upstream.iter_content.return_value = [b'clip']
    stream = MagicMock(return_value=upstream)
    monkeypatch.setattr(routes, 'open_media_stream', stream)
    response = request('GET', '/api/disambiguation/1/asset', headers={'Range': 'bytes=0-3'})
    assert response.status_code == 206
    assert response.headers['content-range'] == 'bytes 0-3/100'
    assert response.headers['accept-ranges'] == 'bytes'
    stream.assert_called_once_with('https://i.imgur.com/video.mp4', range_header='bytes=0-3')
    upstream.close.assert_called_once()
