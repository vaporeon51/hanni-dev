"""Async service boundary for content-set reads."""

from __future__ import annotations

import asyncio

from src.config.constants import MIN_CONTENT_AGE
from src.db.collections import (
    ContentCollection,
    ContentSet,
    CollectionPreview,
    get_collection,
    get_collection_feed,
    get_collection_preview,
)
from src.db.feed import FeedSort


async def load_collection_preview(content_link_id: int) -> CollectionPreview | None:
    return await asyncio.to_thread(get_collection_preview, content_link_id)


async def load_collection(content_link_id: int) -> ContentCollection | None:
    return await asyncio.to_thread(get_collection, content_link_id)


async def load_collection_feed(
    *, query: str | None, sort: FeedSort, limit: int
) -> list[ContentSet]:
    return await asyncio.to_thread(
        get_collection_feed,
        query=query,
        sort=sort,
        limit=limit,
        min_age=MIN_CONTENT_AGE,
    )
