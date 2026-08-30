"""Async service boundary for content-set reads."""

from __future__ import annotations

import asyncio

from src.db.collections import ContentCollection, CollectionPreview, get_collection, get_collection_preview


async def load_collection_preview(content_link_id: int) -> CollectionPreview | None:
    return await asyncio.to_thread(get_collection_preview, content_link_id)


async def load_collection(content_link_id: int) -> ContentCollection | None:
    return await asyncio.to_thread(get_collection, content_link_id)
