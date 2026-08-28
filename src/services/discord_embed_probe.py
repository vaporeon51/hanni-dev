"""Use a private Discord webhook to decide whether a URL embeds successfully."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from src.config.constants import (
    DEAD_LINK_REQUEST_TIMEOUT_SECONDS,
    DISCORD_EMBED_POLL_INTERVAL_SECONDS,
    DISCORD_EMBED_WAIT_SECONDS,
)

ProbeStatus = Literal["live", "dead", "unknown"]
_DISCORD_WEBHOOK_HOSTS = {"discord.com", "ptb.discord.com", "canary.discord.com"}


@dataclass(frozen=True)
class DiscordEmbedProbeResult:
    url: str
    status: ProbeStatus
    embed_type: str | None = None
    error: str | None = None
    embed_pending: bool = False


def post_discord_notice(
    content: str,
    *,
    webhook_url: str,
    session: requests.Session | None = None,
    request_timeout: int = DEAD_LINK_REQUEST_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Post a plain audit notice, returning a safe error string on failure."""

    if not _valid_webhook_url(webhook_url):
        return "invalid Discord webhook URL"
    owns_session = session is None
    session = session or requests.Session()
    try:
        response = _request(
            session,
            "POST",
            _with_wait(webhook_url),
            sleep=sleep,
            json={"content": content[:2000], "allowed_mentions": {"parse": []}},
            timeout=(10, request_timeout),
        )
        try:
            if response.status_code == 429:
                return "Discord webhook rate limited"
            response.raise_for_status()
            return None
        finally:
            response.close()
    except requests.RequestException as error:
        return f"Discord webhook request failed: {_safe_error(error, webhook_url)}"
    finally:
        if owns_session:
            session.close()


def _valid_webhook_url(webhook_url: str) -> bool:
    parsed = urlparse(webhook_url)
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in _DISCORD_WEBHOOK_HOSTS
        and len(parts) >= 4
        and parts[0] == "api"
        and parts[1] == "webhooks"
        and bool(parts[2])
        and bool(parts[3])
    )


def _with_wait(webhook_url: str) -> str:
    parsed = urlparse(webhook_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["wait"] = "true"
    return urlunparse(parsed._replace(query=urlencode(query)))


def _message_url(webhook_url: str, message_id: str) -> str:
    parsed = urlparse(webhook_url)
    return urlunparse(parsed._replace(path=f"{parsed.path.rstrip('/')}/messages/{message_id}", query="", fragment=""))


def _classify_message(url: str, message: object) -> DiscordEmbedProbeResult | None:
    if not isinstance(message, dict):
        return None
    embeds = message.get("embeds")
    if not isinstance(embeds, list) or not embeds:
        return None
    first_embed = embeds[0]
    if not isinstance(first_embed, dict):
        return None
    embed_type = str(first_embed.get("type") or "").lower() or None
    if embed_type == "article":
        return DiscordEmbedProbeResult(
            url=url,
            status="dead",
            embed_type=embed_type,
            error="Discord rendered the URL as an article instead of media",
        )
    return DiscordEmbedProbeResult(url=url, status="live", embed_type=embed_type)


def _safe_error(error: Exception, webhook_url: str) -> str:
    # Request exceptions often include their target URL. Never persist the
    # webhook token in last_check_error or print it in worker logs.
    message = str(error).replace(webhook_url, "<discord webhook>")
    parsed = urlparse(webhook_url)
    if parsed.path:
        message = message.replace(parsed.path, "/api/webhooks/<redacted>")
    return message[:1800]


def _retry_after_seconds(response: requests.Response) -> float:
    try:
        payload = response.json()
        if isinstance(payload, dict) and payload.get("retry_after") is not None:
            return max(0.25, min(float(payload["retry_after"]), 30.0))
    except (TypeError, ValueError, requests.JSONDecodeError):
        pass
    try:
        return max(0.25, min(float(response.headers.get("Retry-After", "1")), 30.0))
    except ValueError:
        return 1.0


def _request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    sleep: Callable[[float], None],
    **kwargs: object,
) -> requests.Response:
    """Make a Discord request and honor a short webhook rate limit."""

    response: requests.Response | None = None
    for attempt in range(3):
        response = session.request(method, url, **kwargs)
        if response.status_code != 429 or attempt == 2:
            return response
        delay = _retry_after_seconds(response)
        response.close()
        sleep(delay)
    assert response is not None  # pragma: no cover - loop always executes
    return response


def probe_discord_embed(
    url: str,
    *,
    webhook_url: str,
    session: requests.Session | None = None,
    wait_seconds: float = DISCORD_EMBED_WAIT_SECONDS,
    poll_interval_seconds: float = DISCORD_EMBED_POLL_INTERVAL_SECONDS,
    request_timeout: int = DEAD_LINK_REQUEST_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DiscordEmbedProbeResult:
    """Post ``url`` and inspect its Discord embed.

    This intentionally preserves the old bot's rule: an ``article`` embed is
    broken and any other first embed is live. No embed is ``unknown`` because
    Discord may still be unfurling it or may be temporarily rate limited.
    """

    if not _valid_webhook_url(webhook_url):
        return DiscordEmbedProbeResult(url=url, status="unknown", error="invalid Discord webhook URL")

    owns_session = session is None
    session = session or requests.Session()
    result: DiscordEmbedProbeResult | None = None
    deadline = monotonic() + max(0.0, wait_seconds)
    try:
        response = _request(
            session,
            "POST",
            _with_wait(webhook_url),
            sleep=sleep,
            json={"content": url, "allowed_mentions": {"parse": []}},
            timeout=(10, request_timeout),
        )
        try:
            if response.status_code == 429:
                return DiscordEmbedProbeResult(url=url, status="unknown", error="Discord webhook rate limited")
            response.raise_for_status()
            message = response.json()
        finally:
            response.close()
        if not isinstance(message, dict) or not message.get("id"):
            return DiscordEmbedProbeResult(url=url, status="unknown", error="Discord did not return a message ID")

        message_id = str(message["id"])
        result = _classify_message(url, message)
        message_endpoint = _message_url(webhook_url, message_id)
        while result is None and monotonic() < deadline:
            remaining = deadline - monotonic()
            sleep(min(max(0.0, poll_interval_seconds), max(0.0, remaining)))
            response = _request(
                session,
                "GET",
                message_endpoint,
                sleep=sleep,
                timeout=(10, request_timeout),
            )
            try:
                if response.status_code == 429:
                    return DiscordEmbedProbeResult(url=url, status="unknown", error="Discord webhook rate limited")
                response.raise_for_status()
                message = response.json()
            finally:
                response.close()
            result = _classify_message(url, message)

        return result or DiscordEmbedProbeResult(
            url=url,
            status="unknown",
            error=f"Discord produced no embed within {max(0.0, wait_seconds):g} seconds",
            embed_pending=True,
        )
    except requests.RequestException as error:
        return DiscordEmbedProbeResult(
            url=url,
            status="unknown",
            error=f"Discord webhook request failed: {_safe_error(error, webhook_url)}",
        )
    except (TypeError, ValueError, requests.JSONDecodeError) as error:
        return DiscordEmbedProbeResult(url=url, status="unknown", error=f"Invalid Discord response: {error}")
    finally:
        if owns_session:
            session.close()
