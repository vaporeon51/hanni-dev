"""FastAPI entrypoint for the Hanni web application."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env.local", override=True)

from src.db import POOL  # noqa: E402
from src.db.analytics import record_country_session, record_link_request  # noqa: E402
from src.db.bias import (  # noqa: E402
    LEADERBOARD_SNAPSHOT_LIMIT,
    get_global_group_leaderboard,
    get_global_leaderboard,
    record_sorter_vote,
)
from src.db.feedback import ContentFeedback, add_content_report, add_content_vote  # noqa: E402
from src.db.media import get_live_content_url  # noqa: E402
from src.services.feed import load_feed, load_role_suggestions  # noqa: E402
from src.services.feed_history import feed_history, link_history, scroll_history  # noqa: E402
from src.services.collections import load_collection, load_collection_feed, load_collection_preview, load_collections_for_url  # noqa: E402
from src.services.dead_link_queue import enqueue_priority_url  # noqa: E402
from src.services.media import (  # noqa: E402
    TRANSIENT_UPSTREAM_STATUSES,
    MediaResolutionError,
    MediaUpstreamError,
    open_media_stream,
    resolve_media_url_cached,
)

templates = Jinja2Templates(directory=str(REPO_ROOT / "templates"))
logger = logging.getLogger(__name__)


def _norm_group_name(value: str | None) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


_GROUP_ALIASES = {"idle": "gidle", "ohmygirl": "omg"}


# Clean host (e.g. bias.hannibee.art): same app, wholesome only. Pages and
# APIs that serve 18+ content don't exist there — pages bounce to the clean
# home, content APIs 404. Override with CLEAN_HOST="" to disable.
CLEAN_HOST = os.getenv("CLEAN_HOST", "bias.hannibee.art").strip().lower()

_CLEAN_PAGE_PATHS = {"/feed", "/sets", "/scroll"}
_CLEAN_BLOCKED_API_PREFIXES = (
    "/api/feed",
    "/api/sets",
    "/api/scroll",
    "/api/collections",
    "/api/link",
    "/api/roles",
)


def _is_clean_host(request: Request) -> bool:
    host = request.headers.get("host", "").split(":")[0].strip().lower()
    return bool(CLEAN_HOST) and host == CLEAN_HOST


def _resolve_group_name(value: str | None) -> str:
    key = _norm_group_name(value)
    if key == _norm_group_name("Girls' Generation"):
        return _norm_group_name("SNSD")
    return _GROUP_ALIASES.get(key, key)


def _load_sorter_photo_maps() -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """Load sorter portrait overrides.

    Returns ``(embed_by_role, local_by_role, embed_by_source, local_by_source,
    group_photos)``:
    - ``embed_*``: Discord-proxied kpopping bytes (preferred — the actual
      kpopping portrait, hotlinkable with no expiry).
    - ``local_*``: vendored same-origin sorter portraits (fallback).
    - ``group_photos``: vendored group shots keyed by normalized group name.
    Missing files (fresh checkout before the build scripts run) fall back to
    database image urls.
    """

    embed_by_role: dict[str, str] = {}
    embed_by_source: dict[str, str] = {}
    try:
        payload = json.loads((REPO_ROOT / "static" / "sorter" / "embed-photos.json").read_text())
        embed_by_role = {str(k): str(v) for k, v in payload.get("by_role", {}).items()}
        embed_by_source = {str(k): str(v) for k, v in payload.get("by_source", {}).items()}
    except (OSError, ValueError):
        pass
    by_role: dict[str, str] = {}
    by_source: dict[str, str] = {}
    group_photos: dict[str, str] = {}
    try:
        payload = json.loads((REPO_ROOT / "static" / "sorter" / "role-photos.json").read_text())
        by_role = {str(role_id): str(url) for role_id, url in payload.items()}
    except (OSError, ValueError):
        logger.warning("role-photos.json missing; leaderboard will use database image urls")
    try:
        catalog = json.loads((REPO_ROOT / "static" / "sorter" / "catalog.json").read_text())
        for entry in catalog.get("entries", []):
            local = entry.get("local")
            if not local:
                continue
            for source in (entry.get("photo"), entry.get("fallback")):
                if source:
                    by_source.setdefault(str(source), str(local))
            if entry.get("kind") == "group" and entry.get("group"):
                group_photos.setdefault(_resolve_group_name(entry.get("group")), str(local))
    except (OSError, ValueError):
        pass
    return embed_by_role, by_role, embed_by_source, by_source, group_photos


EMBED_PHOTOS, ROLE_PHOTOS, EMBED_SOURCES, SOURCE_PHOTOS, GROUP_PHOTOS = _load_sorter_photo_maps()


def _board_image(entry_role_id: str, image_url: str | None) -> str | None:
    if entry_role_id and entry_role_id in EMBED_PHOTOS:
        return EMBED_PHOTOS[entry_role_id]
    if entry_role_id and entry_role_id in ROLE_PHOTOS:
        return ROLE_PHOTOS[entry_role_id]
    if image_url and image_url in EMBED_SOURCES:
        return EMBED_SOURCES[image_url]
    if image_url and image_url in SOURCE_PHOTOS:
        return SOURCE_PHOTOS[image_url]
    return image_url


def _member_image(image_url: str | None) -> str | None:
    """Resolve one top-member portrait to something hotlinkable."""

    if image_url and image_url in EMBED_SOURCES:
        return EMBED_SOURCES[image_url]
    if image_url and image_url in SOURCE_PHOTOS:
        return SOURCE_PHOTOS[image_url]
    return image_url


def _group_image(group_name: str | None, fallback_url: str | None) -> str | None:
    """Prefer the vendored group shot; fall back to the top member portrait."""

    photo = GROUP_PHOTOS.get(_resolve_group_name(group_name))
    if photo:
        return photo
    return _board_image("", fallback_url)


def _serialize_top_members(entry: Any) -> list[dict[str, str | None]]:
    images = list(entry.top_member_images or [])
    return [
        {"name": name, "image_url": _member_image(images[index] if index < len(images) else None)}
        for index, name in enumerate(entry.top_members or [])
    ]

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
SORTER_VOTE_COOLDOWN_SECONDS = 2
SORTER_VOTE_CACHE_CAPACITY = 2048
_sorter_vote_rate_limiter = _RecentActionRateLimiter(
    SORTER_VOTE_COOLDOWN_SECONDS,
    SORTER_VOTE_CACHE_CAPACITY,
)


def _record_link_request_safely(*, found: bool, cycle_reset: bool) -> None:
    try:
        record_link_request(found=found, cycle_reset=cycle_reset)
    except Exception:
        # Analytics should never turn a successful content request into an
        # error, including during a deploy before its migration is applied.
        logger.exception("Could not record /api/link analytics")


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


@app.middleware("http")
async def clean_host_gate(request: Request, call_next):
    request.state.clean = _is_clean_host(request)
    if request.state.clean:
        if request.url.path in _CLEAN_PAGE_PATHS:
            return RedirectResponse(url="/", status_code=307)
        if request.url.path.startswith(_CLEAN_BLOCKED_API_PREFIXES):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
    return await call_next(request)


def _static_version() -> str:
    """Change asset URLs whenever local CSS, JavaScript, or data changes."""

    assets = (
        tuple((REPO_ROOT / "static").rglob("*.css"))
        + tuple((REPO_ROOT / "static").rglob("*.js"))
        + tuple((REPO_ROOT / "static").rglob("*.json"))
        + tuple((REPO_ROOT / "static").rglob("*.svg"))
        + tuple((REPO_ROOT / "static").glob("og-image.png"))
    )
    return str(max(asset.stat().st_mtime_ns for asset in assets))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> Response:
    clean = bool(getattr(request.state, "clean", False))
    if clean:
        # The clean host has no home page — it opens straight into the sorter.
        return RedirectResponse(url="/sorter", status_code=307)
    response = templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"static_version": _static_version(), "clean": clean},
    )
    _ensure_visitor_cookie(request, response)
    return response


@app.get("/feed", response_class=HTMLResponse)
async def feed_page(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="feed.html",
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


@app.get("/sorter", response_class=HTMLResponse)
async def sorter_page(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="sorter.html",
        context={
            "static_version": _static_version(),
            "clean": bool(getattr(request.state, "clean", False)),
        },
    )
    _ensure_visitor_cookie(request, response)
    return response


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="leaderboard.html",
        context={
            "static_version": _static_version(),
            "clean": bool(getattr(request.state, "clean", False)),
        },
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
    background_tasks: BackgroundTasks,
    q: str | None = Query(default=None, max_length=100),
) -> PlainTextResponse:
    """Return one random source URL while avoiding recent results per query."""

    query = q.strip() if q else None
    history_key = " ".join(query.casefold().split())[:100] if query else "__all__"
    recent_urls = link_history.recent_urls(history_key)
    items = await load_feed(
        query=query,
        sort="random",
        limit=1,
        recent_urls=recent_urls,
        exclude_recent=True,
    )
    cycle_reset = False
    if not items and recent_urls:
        # Narrow searches may exhaust their entire pool before the 100-item
        # history fills. Begin a new cycle only when no unseen result remains.
        link_history.clear(history_key)
        cycle_reset = True
        items = await load_feed(
            query=query,
            sort="random",
            limit=1,
            recent_urls=(),
            exclude_recent=True,
        )
    if not items:
        await asyncio.to_thread(
            _record_link_request_safely,
            found=False,
            cycle_reset=cycle_reset,
        )
        raise HTTPException(status_code=404, detail="No content links found")

    item = items[0]
    link_history.remember(history_key, item.url)
    background_tasks.add_task(
        _record_link_request_safely,
        found=True,
        cycle_reset=cycle_reset,
    )
    return PlainTextResponse(
        item.url,
        headers={"Cache-Control": "no-store"},
        background=background_tasks,
    )


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


@app.get("/api/collections/by-url")
async def collection_by_url(
    request: Request,
    response: Response,
    url: str = Query(default="", max_length=2000),
) -> dict[str, Any]:
    """Return the live sets built around rows matching a pasted link."""

    raw = url.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Provide a content link URL")
    visitor_id = _ensure_visitor_cookie(request, response)
    if not _search_rate_limiter.allow(visitor_id, "collections-by-url"):
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {SEARCH_COOLDOWN_SECONDS} seconds before searching again.",
            headers={"Retry-After": str(SEARCH_COOLDOWN_SECONDS)},
        )
    results = await load_collections_for_url(raw)
    return {
        "sets": [
            {
                "collection_of": result.collection_of,
                "label": result.label,
                "count": len(result.items),
                "items": [_serialize_item(item) for item in result.items],
            }
            for result in results
        ],
        "count": len(results),
        "url": raw,
    }


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


@app.post("/api/sorter/vote")
async def sorter_vote(
    request: Request,
    response: Response,
    payload: dict[str, Any] = Body(...),
) -> dict[str, bool]:
    """Record one bias-sorter matchup as an ELO vote (best-effort, always 200)."""

    visitor_token = _ensure_visitor_cookie(request, response)
    try:
        winner_id = str(payload.get("winner_role_id", ""))
        loser_id = str(payload.get("loser_role_id", ""))
    except AttributeError:
        return {"recorded": False}
    if not winner_id or not loser_id or winner_id == loser_id:
        return {"recorded": False}
    if not _sorter_vote_rate_limiter.allow(visitor_token, "sorter-vote"):
        return {"recorded": False}
    try:
        recorded = await asyncio.to_thread(record_sorter_vote, winner_id, loser_id)
    except Exception:
        logger.exception("Could not record sorter vote")
        return {"recorded": False}
    return {"recorded": recorded is not None}


@app.get("/api/leaderboard")
async def leaderboard(
    kind: str = Query(default="idols"),
) -> dict[str, Any]:
    """Global consensus board. The Mine tab renders the visitor's own sorter
    ranking client-side, so it never hits this endpoint."""
    if kind not in {"idols", "groups"}:
        raise HTTPException(status_code=400, detail="kind must be idols or groups")
    try:
        if kind == "idols":
            board = await asyncio.to_thread(
                get_global_leaderboard, LEADERBOARD_SNAPSHOT_LIMIT
            )
            return {
                "scope": "global",
                "kind": kind,
                "vote_count": board.vote_count,
                "movement_baseline_date": board.movement_baseline_date.isoformat()
                if board.movement_baseline_date
                else None,
                "entries": [
                    {
                        "rank": index + 1,
                        "role_id": entry.role_id,
                        "member_name": entry.member_name,
                        "group_name": entry.group_name,
                        "elo": entry.elo,
                        "image_url": _board_image(entry.role_id, entry.image_url),
                        "previous_rank": entry.previous_rank,
                        "votes": entry.votes,
                    }
                    for index, entry in enumerate(board.entries)
                ],
            }
        group_board = await asyncio.to_thread(get_global_group_leaderboard, 15, 3)
        return {
            "scope": "global",
            "kind": kind,
            "vote_count": group_board.vote_count,
            "top_n": group_board.top_n,
            "entries": [
                {
                    "group_name": entry.group_name,
                    "elo": entry.elo,
                    "member_count": entry.member_count,
                    "ranked_member_count": entry.ranked_member_count,
                    "top_members": _serialize_top_members(entry),
                    "image_url": _group_image(entry.group_name, entry.image_url),
                    "votes": entry.votes,
                }
                for entry in group_board.entries
            ],
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Could not load leaderboard")
        raise HTTPException(status_code=503, detail="Leaderboard is catching up. Please retry shortly.")


@app.get("/api/roles")
async def roles(query: str = Query(default="", alias="q", max_length=100)) -> list[dict[str, str | None]]:
    return await load_role_suggestions(query=query)


if __name__ == "__main__":  # pragma: no cover - convenience for local development
    import uvicorn

    uvicorn.run("src.web.app:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=False)
