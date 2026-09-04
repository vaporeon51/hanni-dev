from __future__ import annotations

from dataclasses import replace

from src.db.feed import FeedItem
from src.services import discord_feed_commands as commands


ITEM = FeedItem(
    content_link_id=1,
    role_id="role-1",
    member_name="Hanni",
    group_name="NewJeans",
    url="https://i.imgur.com/example.mp4",
    original_url=None,
    uploaded_date=None,
    score=1.0,
)


def test_parse_feed_command():
    assert commands.parse_feed_command("!feed aespa") == "aespa"
    assert commands.parse_feed_command("  !FEED   hanni newjeans  ") == "hanni newjeans"
    assert commands.parse_feed_command("!feed") == ""
    assert commands.parse_feed_command("hello !feed aespa") is None
    assert commands.parse_feed_command("!feeding aespa") is None


def test_select_feed_item_resets_only_after_small_pool_is_exhausted(monkeypatch):
    calls = []
    commands.discord_feed_history.clear("aespa")
    commands.discord_feed_history.remember("aespa", "https://i.imgur.com/old.mp4")

    def get_items(**kwargs):
        calls.append(kwargs)
        return [] if kwargs["recent_urls"] else [ITEM]

    monkeypatch.setattr(commands, "get_feed_items", get_items)

    assert commands.select_feed_item("Aespa") == ITEM
    assert len(calls) == 2
    assert calls[0]["exclude_recent"] is True
    assert calls[1]["recent_urls"] == ()


def test_listener_primes_cursor_then_processes_new_commands(monkeypatch):
    pages = [
        [{"id": "10", "content": "!feed old", "author": {"bot": False}}],
        [
            {"id": "12", "content": "!feed aespa", "author": {"bot": False}},
            {"id": "11", "content": "ordinary message", "author": {"bot": False}},
            {"id": "13", "content": "!feed ignored", "webhook_id": "1"},
        ],
    ]
    requests = []
    handled = []

    def get_messages(channel_id, *, after_message_id, limit):
        requests.append((channel_id, after_message_id, limit))
        return pages.pop(0)

    monkeypatch.setattr(commands.content_discord, "get_channel_messages", get_messages)
    monkeypatch.setattr(commands, "handle_feed_command", handled.append)
    listener = commands.DiscordFeedCommandListener(channel_id="channel-1")

    assert listener.poll_once() == 0
    assert listener.cursor == "10"
    assert listener.poll_once() == 1
    assert listener.cursor == "13"
    assert handled == ["aespa"]
    assert requests == [("channel-1", None, 1), ("channel-1", "10", 100)]


def test_listener_does_not_skip_first_command_in_initially_empty_channel(monkeypatch):
    pages = [[], [{"id": "20", "content": "!feed", "author": {"bot": False}}]]
    handled = []
    monkeypatch.setattr(
        commands.content_discord,
        "get_channel_messages",
        lambda channel_id, *, after_message_id, limit: pages.pop(0),
    )
    monkeypatch.setattr(commands, "handle_feed_command", handled.append)
    listener = commands.DiscordFeedCommandListener(channel_id="channel-1")

    assert listener.poll_once() == 0
    assert listener.cursor == "0"
    assert listener.poll_once() == 1
    assert handled == [""]


def test_handle_feed_command_posts_and_enqueues(monkeypatch):
    posted = []
    queued = []
    monkeypatch.setattr(commands, "select_feed_item", lambda query: replace(ITEM, role_id=query))
    monkeypatch.setattr(
        commands,
        "post_discord_notice",
        lambda content, *, webhook_url: posted.append((content, webhook_url)),
    )
    monkeypatch.setattr(commands, "DISCORD_FEED_WEBHOOK_URL", "https://discord.com/api/webhooks/1/token")
    monkeypatch.setattr(commands, "enqueue_priority_url", queued.append)

    commands.handle_feed_command("aespa")

    assert posted == [(ITEM.url, "https://discord.com/api/webhooks/1/token")]
    assert queued == [ITEM.url]
