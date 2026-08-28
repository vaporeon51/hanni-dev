"""Resolve a stored content URL to one browser-ready media asset."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

import requests

MediaKind = Literal["video", "image", "link"]
IMGUR_IMAGE_API = "https://api.imgur.com/3/image/{media_id}"
IMGUR_HOSTS = {"imgur.com", "www.imgur.com", "i.imgur.com"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}


@dataclass(frozen=True)
class ResolvedMedia:
    kind: MediaKind
    url: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _extension(url: str) -> str:
    path = urlsplit(url).path.lower()
    dot = path.rfind(".")
    return path[dot:] if dot >= 0 else ""


def _imgur_id(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in IMGUR_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1:
        return None
    media_id = parts[0].split(".", 1)[0]
    return media_id if media_id.isalnum() else None


def _safe_imgur_asset(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in IMGUR_HOSTS:
        return None
    return value


def resolve_media_url(
    url: str,
    *,
    client_id: str | None = None,
    session: requests.Session | None = None,
) -> ResolvedMedia:
    """Resolve direct media immediately and use Imgur metadata for page URLs.

    A single authenticated metadata request replaces the old browser behavior
    of probing several possible file extensions for every item.
    """

    extension = _extension(url)
    if extension in VIDEO_EXTENSIONS:
        return ResolvedMedia("video", url)
    if extension in IMAGE_EXTENSIONS:
        return ResolvedMedia("image", url)

    media_id = _imgur_id(url)
    resolved_client_id = (client_id if client_id is not None else os.getenv("IMGUR_CLIENT_ID", "")).strip()
    if not media_id or not resolved_client_id:
        return ResolvedMedia("link", url)

    requester = session or requests.Session()
    should_close = session is None
    try:
        response = requester.get(
            IMGUR_IMAGE_API.format(media_id=media_id),
            headers={
                "Authorization": f"Client-ID {resolved_client_id}",
                "User-Agent": "hanni-web/1.0",
            },
            timeout=(5, 12),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return ResolvedMedia("link", url)
    finally:
        if should_close:
            requester.close()

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return ResolvedMedia("link", url)

    mp4_url = _safe_imgur_asset(data.get("mp4"))
    if mp4_url:
        return ResolvedMedia("video", mp4_url)

    direct_url = _safe_imgur_asset(data.get("link"))
    if direct_url:
        kind: MediaKind = "video" if _extension(direct_url) in VIDEO_EXTENSIONS else "image"
        return ResolvedMedia(kind, direct_url)
    return ResolvedMedia("link", url)


@lru_cache(maxsize=512)
def resolve_media_url_cached(url: str) -> ResolvedMedia:
    return resolve_media_url(url)
