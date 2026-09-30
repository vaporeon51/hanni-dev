"""Unlisted label-review page and its media routes."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.db.disambiguation import get_review_set, list_review_sets, mark_broken, save_review
from src.db.media import get_live_content_url
from src.services.media import (
    MediaResolutionError, MediaUnavailableError, MediaUpstreamError,
    open_media_stream, resolve_media_url_cached,
)

def require_same_site(request: Request):
    if request.headers.get('x-admin-review') != '1' or request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Use the admin review page to save')


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / 'templates'))


@router.get('/disambiguation')
def review_page(request: Request):
    response = templates.TemplateResponse(request=request, name='disambiguation.html',
        context={'static_version': str(max(p.stat().st_mtime_ns for p in
                    (Path(__file__).resolve().parents[2] / 'static').glob('*') if p.suffix in {'.js', '.css'}))})
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@router.get('/api/disambiguation')
def queue():
    return {'sets': list_review_sets()}


@router.get('/api/disambiguation/{anchor_id}')
def review_set(anchor_id: int):
    result = get_review_set(anchor_id)
    if result is None:
        raise HTTPException(404, 'Set not found')
    return result


@router.get('/api/disambiguation/{content_link_id}/media')
def media(content_link_id: int):
    url = get_live_content_url(content_link_id)
    if url is None:
        raise HTTPException(404, 'Image no longer exists')
    try:
        resolved = resolve_media_url_cached(url)
    except (MediaUnavailableError, MediaResolutionError) as error:
        raise HTTPException(503, 'Preview unavailable. Retry, open the original, or mark it as broken if confirmed.') from error
    return {'kind': resolved.kind, 'url': f'/api/disambiguation/{content_link_id}/asset'}


@router.get('/api/disambiguation/{content_link_id}/asset')
def asset(content_link_id: int, request: Request):
    url = get_live_content_url(content_link_id)
    if url is None:
        raise HTTPException(404, 'Image no longer exists')
    try:
        resolved = resolve_media_url_cached(url)
        if resolved.kind not in {'image', 'video'}:
            raise HTTPException(404, 'No playable preview available')
        upstream = open_media_stream(resolved.url, range_header=request.headers.get('range'))
    except (MediaUnavailableError, MediaResolutionError, MediaUpstreamError) as error:
        raise HTTPException(502, 'Preview host is unavailable') from error
    headers = {'Cache-Control': 'private, max-age=300'}
    for header in ('Content-Length', 'Content-Range', 'Accept-Ranges'):
        if upstream.headers.get(header):
            headers[header] = upstream.headers[header]
    def chunks():
        try:
            yield from upstream.iter_content(chunk_size=64 * 1024)
        finally:
            upstream.close()
    return StreamingResponse(chunks(), status_code=upstream.status_code,
                             media_type=upstream.headers.get('Content-Type', 'application/octet-stream'), headers=headers)


class Selection(BaseModel):
    selected: list[str] = Field(max_length=100)
    expected: list[str] = Field(min_length=1, max_length=100)


@router.post('/api/disambiguation/{content_link_id}', dependencies=[Depends(require_same_site)])
def save(content_link_id: int, selection: Selection):
    try:
        updated = save_review(content_link_id, selection.selected, selection.expected)
    except LookupError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {'updated': updated}


@router.post('/api/disambiguation/{content_link_id}/broken', dependencies=[Depends(require_same_site)])
def broken(content_link_id: int):
    try:
        return {'updated': mark_broken(content_link_id)}
    except LookupError as error:
        raise HTTPException(404, str(error)) from error

