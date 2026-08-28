"""FastAPI entrypoint for the Hanni web application."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env.local", override=True)

from src.db import POOL  # noqa: E402
from src.db.feedback import ContentFeedback, add_content_report, add_content_vote  # noqa: E402
from src.db.media import get_live_content_url  # noqa: E402
from src.services.feed import load_feed, load_role_suggestions  # noqa: E402
from src.services.media import MediaUpstreamError, open_media_stream, resolve_media_url_cached  # noqa: E402

templates = Jinja2Templates(directory=str(REPO_ROOT / "templates"))

VISITOR_COOKIE = "hanni_visitor"
FEEDBACK_COOLDOWN_SECONDS = 5 * 60
FEEDBACK_CACHE_CAPACITY = 20
SEARCH_COOLDOWN_SECONDS = 10
SEARCH_CACHE_CAPACITY = 256


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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(request=request, name="index.html", context={})
    _ensure_visitor_cookie(request, response)
    return response


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


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


@app.get("/api/feed")
async def feed(
    request: Request,
    response: Response,
    query: str | None = Query(default=None, max_length=100),
    sort: str = Query(default="random"),
    limit: int = Query(default=15, ge=1, le=30),
) -> dict[str, Any]:
    if sort not in {"random", "latest", "oldest", "top"}:
        raise HTTPException(status_code=400, detail="sort must be random, latest, oldest, or top")
    visitor_id = _ensure_visitor_cookie(request, response)
    if not _search_rate_limiter.allow(visitor_id, "feed"):
        raise HTTPException(
            status_code=429,
            detail="Please wait 10 seconds before searching again.",
            headers={"Retry-After": str(SEARCH_COOLDOWN_SECONDS)},
        )
    items = await load_feed(query=query, sort=sort, limit=limit)
    return {"items": [_serialize_item(item) for item in items], "count": len(items), "sort": sort, "query": query}


@app.get("/api/feed/{content_link_id}/media")
async def media(content_link_id: int) -> dict[str, str]:
    url = await asyncio.to_thread(get_live_content_url, content_link_id)
    if url is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    resolved = await asyncio.to_thread(resolve_media_url_cached, url)
    if resolved.kind in {"video", "image"}:
        return {"kind": resolved.kind, "url": f"/api/feed/{content_link_id}/asset"}
    return resolved.as_dict()


@app.get("/api/feed/{content_link_id}/asset")
def media_asset(content_link_id: int, request: Request) -> StreamingResponse:
    """Proxy one live, allowlisted asset so browsers do not hotlink its CDN."""

    url = get_live_content_url(content_link_id)
    if url is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    resolved = resolve_media_url_cached(url)
    if resolved.kind not in {"video", "image"}:
        raise HTTPException(status_code=404, detail="Media asset is unavailable")

    try:
        upstream = open_media_stream(resolved.url, range_header=request.headers.get("range"))
    except MediaUpstreamError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

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
