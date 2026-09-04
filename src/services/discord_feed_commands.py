"""Listen for ``!feed`` commands using the configured read-only Discord user."""

from __future__ import annotations

import asyncio
import re
import random
from typing import Any

from src import content_discord
from src.config.constants import (
    DISCORD_FEED_CHANNEL_ID,
    DISCORD_FEED_WEBHOOK_URL,
    MIN_CONTENT_AGE,
)
from src.db.feed import FeedItem, get_feed_items
from src.db.locks import advisory_lock
from src.services.dead_link_queue import enqueue_priority_url
from src.services.discord_embed_probe import post_discord_notice
from src.services.feed_history import discord_feed_history


COMMAND_POLL_SECONDS = 2.0
_FEED_COMMAND = re.compile(r"^\s*!feed(?:\s+(.*?))?\s*$", re.IGNORECASE)


def parse_feed_command(content: object) -> str | None:
    """Return the optional query, or ``None`` when this is not a command."""

    if not isinstance(content, str):
        return None
    match = _FEED_COMMAND.fullmatch(content)
    if match is None:
        return None
    return (match.group(1) or "").strip()[:100]


def _history_key(query: str) -> str:
    normalized = " ".join(query.casefold().split())
    return normalized or "__all__"


def select_feed_item(query: str) -> FeedItem | None:
    """Select one random item with bounded, per-query repeat protection."""

    history_key = _history_key(query)
    recent_urls = discord_feed_history.recent_urls(history_key)
    items = get_feed_items(
        query=query or None,
        sort="random",
        limit=1,
        min_age=MIN_CONTENT_AGE,
        recent_urls=recent_urls,
        exclude_recent=True,
    )
    if not items and recent_urls:
        discord_feed_history.clear(history_key)
        items = get_feed_items(
            query=query or None,
            sort="random",
            limit=1,
            min_age=MIN_CONTENT_AGE,
            recent_urls=(),
            exclude_recent=True,
        )
    if not items:
        return None
    item = items[0]
    discord_feed_history.remember(history_key, item.url)
    return item


def handle_feed_command(query: str) -> None:
    """Resolve one command and post its response through the feed webhook."""

    item = select_feed_item(query)
    if item is None:
        description = f" for `{query}`" if query else ""
        error = post_discord_notice(
            f"No content found{description}.",
            webhook_url=DISCORD_FEED_WEBHOOK_URL,
        )
    else:
        error = post_discord_notice(item.url, webhook_url=DISCORD_FEED_WEBHOOK_URL)
        if error is None:
            enqueue_priority_url(item.url)
    if error:
        print(f"Discord feed response failed: {error}", flush=True)


def _message_id(message: dict[str, Any]) -> int:
    try:
        return int(message.get("id", 0))
    except (TypeError, ValueError):
        return 0


def _is_user_message(message: dict[str, Any]) -> bool:
    if message.get("webhook_id"):
        return False
    author = message.get("author")
    return not isinstance(author, dict) or not bool(author.get("bot"))


class DiscordFeedCommandListener:
    """Keep an in-memory Discord cursor and process each new command once."""

    def __init__(self, *, channel_id: str = DISCORD_FEED_CHANNEL_ID) -> None:
        self.channel_id = channel_id
        self.cursor: str | None = None

    def poll_once(self) -> int:
        messages = content_discord.get_channel_messages(
            self.channel_id,
            after_message_id=self.cursor,
            limit=100 if self.cursor else 1,
        )
        if self.cursor is None:
            # Start at "now" so a deploy never replays an old command.
            self.cursor = str(max((_message_id(message) for message in messages), default=0))
            return 0
        if not messages:
            return 0

        ordered = sorted(messages, key=_message_id)
        handled = 0
        for message in ordered:
            message_id = _message_id(message)
            if message_id <= _message_id({"id": self.cursor}):
                continue
            # Advance before handling: webhook uncertainty must not duplicate a
            # response on the next poll.
            self.cursor = str(message_id)
            if not _is_user_message(message):
                continue
            query = parse_feed_command(message.get("content"))
            if query is None:
                continue
            handle_feed_command(query)
            handled += 1
        return handled


async def discord_feed_command_loop() -> None:
    """Run one active listener across every web/worker process.

    Heroku may start multiple Uvicorn workers inside one dyno. Each process has
    its own cursor, so a Postgres advisory lock elects one active listener and
    lets a standby take over if that process exits.
    """

    if not DISCORD_FEED_CHANNEL_ID or not DISCORD_FEED_WEBHOOK_URL:
        return
    while True:
        with advisory_lock("hanni:discord-feed-command-listener") as acquired:
            if acquired:
                listener = DiscordFeedCommandListener()
                print(f"Discord feed listener active for channel {DISCORD_FEED_CHANNEL_ID}.", flush=True)
                while True:
                    try:
                        await asyncio.to_thread(listener.poll_once)
                    except Exception as error:
                        print(f"Discord feed listener failed: {type(error).__name__}: {error}", flush=True)
                    await asyncio.sleep(COMMAND_POLL_SECONDS + random.random())
        await asyncio.sleep(COMMAND_POLL_SECONDS + random.random())
