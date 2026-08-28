"""Async service boundary for feed reads."""

from __future__ import annotations

import asyncio

from src.config.constants import MIN_CONTENT_AGE
from src.db.feed import FeedItem, FeedSort, get_feed_items, get_role_suggestions


async def load_feed(
    *, query: str | None, sort: FeedSort, limit: int, recent_urls: tuple[str, ...] = ()
) -> list[FeedItem]:
    return await asyncio.to_thread(
        get_feed_items,
        query=query,
        sort=sort,
        limit=limit,
        min_age=MIN_CONTENT_AGE,
        recent_urls=recent_urls,
    )


async def load_role_suggestions(*, query: str, limit: int = 8) -> list[dict[str, str | None]]:
    return await asyncio.to_thread(get_role_suggestions, query=query, limit=limit, min_age=MIN_CONTENT_AGE)
