"""FastAPI entrypoint for the Hanni web application."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env.local", override=True)

from src.db import POOL  # noqa: E402
from src.db.analytics import record_country_session  # noqa: E402
from src.db.feedback import ContentFeedback, add_content_report, add_content_vote  # noqa: E402
from src.db.media import get_live_content_url  # noqa: E402
from src.services.feed import load_feed, load_role_suggestions  # noqa: E402
from src.services.feed_history import feed_history, link_history, scroll_history  # noqa: E402
from src.services.collections import load_collection, load_collection_feed, load_collection_preview  # noqa: E402
from src.services.dead_link_queue import enqueue_priority_url  # noqa: E402
from src.services.media import (  # noqa: E402
    TRANSIENT_UPSTREAM_STATUSES,
    MediaResolutionError,
    MediaUpstreamError,
    open_media_stream,
    resolve_media_url_cached,
)

templates = Jinja2Templates(directory=str(REPO_ROOT / "templates"))

VISITOR_COOKIE = "hanni_visitor"
ANALYTICS_SESSION_COOKIE = "hanni_analytics_session"
ANALYTICS_SESSION_SECONDS = 30 * 60
FEEDBACK_COOLDOWN_SECONDS = 5 * 60
FEEDBACK_CACHE_CAPACITY = 20
SEARCH_COOLDOWN_SECONDS = 3
SEARCH_CACHE_CAPACITY = 256
SCROLL_COOLDOWN_SECONDS = 1
SCROLL_CACHE_CAPACITY = 512
ANALYTICS_CACHE_CAPACITY = 4096
class _RecentActionRateLimiter:
    """Small in-memory cooldown cache for anonymous browser actions.

    The Discord implementation keyed this cooldown by Discord user and URL.
    The web app has no account system yet, so a private browser cookie provides
    the equivalent per-visitor key for this deliberately lightweight surface.
    """

    def __init__(self, cooldown_seconds: float, capacity: int) -> None:
        self.cooldown_seconds = cooldown_seconds
        self.capacity = capacity
        self._entries: OrderedDict[tuple[str, str], float] = OrderedDict()

    def allow(self, visitor_id: str, action_key: str | int) -> bool:
        now = time.monotonic()
        key = (visitor_id, str(action_key))
        previous = self._entries.get(key)
        if previous is not None and now - previous < self.cooldown_seconds:
            self._entries.move_to_end(key)
            return False

        self._entries[key] = now
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
        return True


_vote_rate_limiter = _RecentActionRateLimiter(FEEDBACK_COOLDOWN_SECONDS, FEEDBACK_CACHE_CAPACITY)
_report_rate_limiter = _RecentActionRateLimiter(FEEDBACK_COOLDOWN_SECONDS, FEEDBACK_CACHE_CAPACITY)
_search_rate_limiter = _RecentActionRateLimiter(SEARCH_COOLDOWN_SECONDS, SEARCH_CACHE_CAPACITY)
_scroll_rate_limiter = _RecentActionRateLimiter(SCROLL_COOLDOWN_SECONDS, SCROLL_CACHE_CAPACITY)
_analytics_rate_limiter = _RecentActionRateLimiter(
    ANALYTICS_SESSION_SECONDS,
    ANALYTICS_CACHE_CAPACITY,
)


def _ensure_visitor_cookie(request: Request, response: Response) -> str:
    visitor_id = request.cookies.get(VISITOR_COOKIE, "")
    if not visitor_id or len(visitor_id) > 128:
        visitor_id = secrets.token_urlsafe(24)
        response.set_cookie(
            VISITOR_COOKIE,
            visitor_id,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
    return visitor_id


def _serialize_feedback(feedback: ContentFeedback) -> dict[str, int | bool]:
    return {
        "upvotes": feedback.upvotes,
        "downvotes": feedback.downvotes,
        "reports": feedback.reports,
        "vote_score": feedback.score,
    }


def _country_code(value: str) -> str:
    normalized = value.strip().upper()
    return normalized if len(normalized) == 2 and normalized.isascii() and normalized.isalpha() else "XX"


def _set_analytics_session_cookie(request: Request, response: Response) -> None:
    response.set_cookie(
        ANALYTICS_SESSION_COOKIE,
        secrets.token_urlsafe(12),
        max_age=ANALYTICS_SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


def _background_tasks_enabled() -> bool:
    return os.getenv("RUN_BACKGROUND_TASKS", "false").strip().lower() in {"1", "true", "yes", "on"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    POOL.open()
    scheduler_task: asyncio.Task[Any] | None = None
    if _background_tasks_enabled():
        from src.worker import scheduler_loop

        scheduler_task = asyncio.create_task(scheduler_loop(), name="hanni-background-scheduler")
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task, return_exceptions=True)
        POOL.close()


app = FastAPI(title="Hanni", description="A web feed for ingested and recovered content.", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(REPO_ROOT / "static")), name="static")


def _static_version() -> str:
    """Change asset URLs whenever local CSS or JavaScript changes."""

    assets = tuple((REPO_ROOT / "static").glob("*.css")) + tuple((REPO_ROOT / "static").glob("*.js"))
    return str(max(asset.stat().st_mtime_ns for asset in assets))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"static_version": _static_version()},
    )
    _ensure_visitor_cookie(request, response)
    return response


@app.get("/sets", response_class=HTMLResponse)
async def sets_page(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="sets.html",
        context={"static_version": _static_version()},
    )
    _ensure_visitor_cookie(request, response)
    return response


@app.get("/scroll", response_class=HTMLResponse)
async def scroll_page(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="scroll.html",
        context={"static_version": _static_version()},
    )
    _ensure_visitor_cookie(request, response)
    return response


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.post("/api/analytics/session")
async def analytics_session(
    request: Request,
    response: Response,
    country_code: str = Body(default="XX", embed=True, max_length=16),
) -> dict[str, bool]:
    """Count one approximate browser-locale session per rolling 30 minutes."""

    visitor_id = _ensure_visitor_cookie(request, response)
    existing_session = request.cookies.get(ANALYTICS_SESSION_COOKIE)
    _set_analytics_session_cookie(request, response)
    if existing_session or not _analytics_rate_limiter.allow(visitor_id, "session"):
        return {"recorded": False}
    await asyncio.to_thread(record_country_session, _country_code(country_code))
    return {"recorded": True}


def _serialize_item(item) -> dict[str, Any]:
    return {
        "content_link_id": item.content_link_id,
        "role_id": item.role_id,
        "member_name": item.member_name,
        "group_name": item.group_name,
        "label": item.label,
        "url": item.url,
        "original_url": item.original_url,
        "uploaded_date": item.uploaded_date.isoformat() if item.uploaded_date else None,
        "score": item.score,
        "upvotes": item.upvotes,
        "downvotes": item.downvotes,
        "vote_score": item.upvotes - item.downvotes,
        "recovered_at": item.recovered_at.isoformat() if item.recovered_at else None,
        "recovery_generation": item.recovery_generation,
    }


def _encode_set_cursor(set_date: datetime | None, anchor_id: int) -> str | None:
    if set_date is None:
        return None
    if set_date.tzinfo is None:
        set_date = set_date.replace(tzinfo=timezone.utc)
    else:
        set_date = set_date.astimezone(timezone.utc)
    return f"{set_date.isoformat()}|{anchor_id}"


def _decode_set_cursor(value: str) -> tuple[datetime, int]:
    try:
        date_text, id_text = value.rsplit("|", 1)
        set_date = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
        anchor_id = int(id_text)
        if anchor_id <= 0:
            raise ValueError
        if set_date.tzinfo is not None:
            set_date = set_date.astimezone(timezone.utc).replace(tzinfo=None)
        return set_date, anchor_id
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="invalid set cursor") from error


@app.get("/api/feed")
async def feed(
    request: Request,
    response: Response,
    query: str | None = Query(default=None, max_length=100),
    sort: str = Query(default="random"),
    limit: int = Query(default=5, ge=1, le=30),
    continuation: bool = Query(default=False),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> dict[str, Any]:
    if sort not in {"random", "latest", "oldest", "top"}:
        raise HTTPException(status_code=400, detail="sort must be random, latest, oldest, or top")
    visitor_id = _ensure_visitor_cookie(request, response)
    rate_limiter = _scroll_rate_limiter if continuation else _search_rate_limiter
    rate_key = "feed-page" if continuation else "feed"
    if not rate_limiter.allow(visitor_id, rate_key):
        cooldown = SCROLL_COOLDOWN_SECONDS if continuation else SEARCH_COOLDOWN_SECONDS
        raise HTTPException(
            status_code=429,
            detail=(
                "Please wait a moment before loading more."
                if continuation
                else f"Please wait {SEARCH_COOLDOWN_SECONDS} seconds before searching again."
            ),
            headers={"Retry-After": str(cooldown)},
        )
    recent_urls = feed_history.recent_urls(visitor_id) if sort == "random" else ()
    if sort == "random":
        items = await load_feed(
            query=query,
            sort=sort,
            limit=limit,
            recent_urls=recent_urls,
            exclude_recent=bool(recent_urls),
        )
    else:
        items = await load_feed(
            query=query,
            sort=sort,
            limit=limit,
            recent_urls=(),
            exclude_recent=False,
            offset=offset,
        )
    if not items and recent_urls:
        feed_history.clear(visitor_id)
        items = await load_feed(query=query, sort=sort, limit=limit, recent_urls=(), exclude_recent=True)
    if sort == "random":
        for item in items:
            feed_history.remember(visitor_id, item.url)
    return {
        "items": [_serialize_item(item) for item in items],
        "count": len(items),
        "has_more": bool(items),
        "sort": sort,
        "query": query,
    }


@app.get("/api/sets")
async def sets(
    request: Request,
    response: Response,
    query: str | None = Query(default=None, max_length=100),
    sort: str = Query(default="latest"),
    limit: int = Query(default=5, ge=1, le=30),
    cursor: str | None = Query(default=None, max_length=100),
) -> dict[str, Any]:
    if sort not in {"random", "latest", "oldest", "top"}:
        raise HTTPException(status_code=400, detail="sort must be random, latest, oldest, or top")
    if cursor is not None and sort not in {"latest", "oldest"}:
        raise HTTPException(status_code=400, detail="set pagination requires newest or oldest sorting")
    cursor_date, cursor_id = _decode_set_cursor(cursor) if cursor else (None, None)
    visitor_id = _ensure_visitor_cookie(request, response)
    if cursor is None and not _search_rate_limiter.allow(visitor_id, "sets"):
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {SEARCH_COOLDOWN_SECONDS} seconds before searching again.",
            headers={"Retry-After": str(SEARCH_COOLDOWN_SECONDS)},
        )
    results = await load_collection_feed(
        query=query,
        sort=sort,
        limit=limit + 1,
        cursor_date=cursor_date,
        cursor_id=cursor_id,
    )
    page = results[:limit]
    next_cursor = (
        _encode_set_cursor(page[-1].set_date, page[-1].collection_of)
        if len(results) > limit and page
        else None
    )
    return {
        "sets": [
            {
                "collection_of": result.collection_of,
                "label": result.label,
                "count": len(result.items),
                "items": [_serialize_item(item) for item in result.items],
            }
            for result in page
        ],
        "count": len(page),
        "sort": sort,
        "query": query,
        "next_cursor": next_cursor,
    }


@app.get("/api/scroll")
async def scroll_feed(
    request: Request,
    response: Response,
    query: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=8, ge=1, le=12),
) -> dict[str, Any]:
    """Return a small random batch for the continuously prefetched reel view."""

    visitor_id = _ensure_visitor_cookie(request, response)
    if not _scroll_rate_limiter.allow(visitor_id, "scroll"):
        raise HTTPException(
            status_code=429,
            detail="Please wait a moment before loading more.",
            headers={"Retry-After": str(SCROLL_COOLDOWN_SECONDS)},
        )
    recent_urls = scroll_history.recent_urls(visitor_id)
    items = await load_feed(
        query=query,
        sort="random",
        limit=limit,
        recent_urls=recent_urls,
        exclude_recent=True,
    )
    cycle_reset = False
    if not items and recent_urls:
        # A narrow search can contain fewer than 100 live links. Only begin a
        # new cycle after every currently remembered URL has been excluded and
        # the query has no unseen result left.
        scroll_history.clear(visitor_id)
        items = await load_feed(
            query=query,
            sort="random",
            limit=limit,
            recent_urls=(),
            exclude_recent=True,
        )
        cycle_reset = True
    # Reserve the complete batch immediately. The scroll UI prefetches before
    # each item is displayed, so waiting for media resolution could otherwise
    # allow the next batch to select the same URL again.
    for item in items:
        scroll_history.remember(visitor_id, item.url)
    return {
        "items": [_serialize_item(item) for item in items],
        "count": len(items),
        "query": query,
        "cycle_reset": cycle_reset,
    }


@app.get("/api/link", response_class=PlainTextResponse)
async def random_content_link(
    q: str | None = Query(default=None, max_length=100),
) -> PlainTextResponse:
    """Return one random source URL while avoiding recent results per query."""

    query = q.strip() if q else None
    history_key = query.casefold() if query else "__all__"
    recent_urls = link_history.recent_urls(history_key)
    items = await load_feed(
        query=query,
        sort="random",
        limit=1,
        recent_urls=recent_urls,
        exclude_recent=True,
    )
    if not items and recent_urls:
        # Narrow searches may exhaust their entire pool before the 100-item
        # history fills. Begin a new cycle only when no unseen result remains.
        link_history.clear(history_key)
        items = await load_feed(
            query=query,
            sort="random",
            limit=1,
            recent_urls=(),
            exclude_recent=True,
        )
    if not items:
        raise HTTPException(status_code=404, detail="No content links found")

    item = items[0]
    link_history.remember(history_key, item.url)
    return PlainTextResponse(item.url, headers={"Cache-Control": "no-store"})


@app.get("/api/feed/{content_link_id}/media")
async def media(content_link_id: int, request: Request, response: Response) -> dict[str, str | int]:
    preview = await load_collection_preview(content_link_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    url = preview.url
    # This endpoint is requested as each delayed feed card is actually shown.
    visitor_id = _ensure_visitor_cookie(request, response)
    feed_history.remember(visitor_id, url)
    enqueue_priority_url(url)
    try:
        resolved = await asyncio.to_thread(resolve_media_url_cached, url)
    except MediaResolutionError as error:
        raise HTTPException(
            status_code=503,
            detail="Media host is catching up. Please retry shortly.",
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from error
    if resolved.kind in {"video", "image"}:
        return {
            "kind": resolved.kind,
            "url": f"/api/feed/{content_link_id}/asset",
            "collection_count": preview.count,
        }
    return {**resolved.as_dict(), "collection_count": preview.count}


@app.get("/api/collections/{content_link_id}")
async def collection(content_link_id: int) -> dict[str, Any]:
    result = await load_collection(content_link_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Content set not found")
    return {
        "items": [_serialize_item(item) for item in result.items],
        "count": len(result.items),
        "label": result.label,
        "collection_of": content_link_id,
    }


@app.get("/api/feed/{content_link_id}/asset")
def media_asset(content_link_id: int, request: Request) -> StreamingResponse:
    """Proxy one live, allowlisted asset so browsers do not hotlink its CDN."""

    url = get_live_content_url(content_link_id)
    if url is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    try:
        resolved = resolve_media_url_cached(url)
    except MediaResolutionError as error:
        raise HTTPException(
            status_code=503,
            detail="Media host is catching up. Please retry shortly.",
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from error
    if resolved.kind not in {"video", "image"}:
        raise HTTPException(status_code=404, detail="Media asset is unavailable")

    try:
        upstream = open_media_stream(resolved.url, range_header=request.headers.get("range"))
    except MediaUpstreamError as error:
        status_code = 503 if error.status_code in TRANSIENT_UPSTREAM_STATUSES else 502
        headers = {"Retry-After": str(error.retry_after_seconds)} if status_code == 503 else None
        raise HTTPException(status_code=status_code, detail=str(error), headers=headers) from error

    response_headers = {"Cache-Control": "public, max-age=3600"}
    for header in ("Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified"):
        value = upstream.headers.get(header)
        if value:
            response_headers[header] = value

    def chunks():
        try:
            yield from upstream.iter_content(chunk_size=64 * 1024)
        finally:
            upstream.close()

    return StreamingResponse(
        chunks(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type", "application/octet-stream"),
        headers=response_headers,
    )


async def _run_feedback_action(
    *,
    request: Request,
    response: Response,
    content_link_id: int,
    action: Literal["vote", "report"],
    direction: Literal["up", "down"] | None = None,
    report_reason: Literal["dead_link", "wrong_idol"] | None = None,
) -> dict[str, int | bool]:
    visitor_id = _ensure_visitor_cookie(request, response)
    limiter = _vote_rate_limiter if action == "vote" else _report_rate_limiter
    if not limiter.allow(visitor_id, content_link_id):
        raise HTTPException(
            status_code=429,
            detail="You've already submitted feedback for this link recently.",
            headers={"Retry-After": str(FEEDBACK_COOLDOWN_SECONDS)},
        )

    if action == "vote":
        feedback = await asyncio.to_thread(add_content_vote, content_link_id, direction or "")
    else:
        feedback = await asyncio.to_thread(add_content_report, content_link_id, report_reason or "")
    if feedback is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    return _serialize_feedback(feedback)


@app.post("/api/feed/{content_link_id}/vote/{direction}")
async def vote(
    content_link_id: int,
    direction: Literal["up", "down"],
    request: Request,
    response: Response,
) -> dict[str, int | bool]:
    return await _run_feedback_action(
        request=request,
        response=response,
        content_link_id=content_link_id,
        action="vote",
        direction=direction,
    )


@app.post("/api/feed/{content_link_id}/report")
async def report(
    content_link_id: int,
    request: Request,
    response: Response,
    reason: Literal["dead_link", "wrong_idol"] = Query(...),
) -> dict[str, int | bool]:
    return await _run_feedback_action(
        request=request,
        response=response,
        content_link_id=content_link_id,
        action="report",
        report_reason=reason,
    )


@app.get("/api/roles")
async def roles(query: str = Query(default="", alias="q", max_length=100)) -> list[dict[str, str | None]]:
    return await load_role_suggestions(query=query)


if __name__ == "__main__":  # pragma: no cover - convenience for local development
    import uvicorn

    uvicorn.run("src.web.app:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=False)
