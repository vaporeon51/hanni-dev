"""Resolve a stored content URL to one browser-ready media asset."""

from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

import requests

from src.config.constants import EPHEMERAL_MEDIA_HOSTS

MediaKind = Literal["video", "image", "link"]
IMGUR_IMAGE_API = "https://api.imgur.com/3/image/{media_id}"
IMGUR_ALBUM_IMAGES_API = "https://api.imgur.com/3/album/{media_id}/images"
IMGUR_HOSTS = {"imgur.com", "www.imgur.com", "i.imgur.com"}
# These durable CDN hosts may be streamed through the public feed asset endpoint.
# Keep this narrower than the worker's URL-check allowlist: every host here is an
# SSRF boundary for a user-accessible proxy.
PROXIED_MEDIA_HOSTS = IMGUR_HOSTS | {"cdn.goyangi.pics", "cdn.kpopping.com"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
TRANSIENT_UPSTREAM_STATUSES = {429, 502, 503, 504}


class MediaUpstreamError(RuntimeError):
    """An allowlisted host did not return a browser-playable media response."""


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


def _imgur_album_id(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in IMGUR_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].lower() != "a":
        return None
    # Imgur accepts both /a/<id> and /a/<title-slug>-<id>.
    media_id = parts[1].split(".", 1)[0].rsplit("-", 1)[-1]
    return media_id if media_id.isalnum() else None


def _safe_imgur_asset(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in IMGUR_HOSTS:
        return None
    return value


def _safe_proxied_asset(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in PROXIED_MEDIA_HOSTS:
        return None
    return value


def _resolved_imgur_item(data: object) -> ResolvedMedia | None:
    if not isinstance(data, dict):
        return None

    mp4_url = _safe_imgur_asset(data.get("mp4"))
    if mp4_url:
        return ResolvedMedia("video", mp4_url)

    direct_url = _safe_imgur_asset(data.get("link"))
    if not direct_url:
        return None
    media_type = str(data.get("type") or "").lower()
    kind: MediaKind = (
        "video"
        if _extension(direct_url) in VIDEO_EXTENSIONS or media_type.startswith("video/")
        else "image"
    )
    return ResolvedMedia(kind, direct_url)


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

    if (urlsplit(url).hostname or "").lower() in EPHEMERAL_MEDIA_HOSTS:
        return ResolvedMedia("link", url)

    extension = _extension(url)
    if extension in VIDEO_EXTENSIONS:
        return ResolvedMedia("video", url)
    if extension in IMAGE_EXTENSIONS:
        return ResolvedMedia("image", url)

    media_id = _imgur_id(url)
    album_id = _imgur_album_id(url)
    resolved_client_id = (client_id if client_id is not None else os.getenv("IMGUR_CLIENT_ID", "")).strip()
    if not (media_id or album_id) or not resolved_client_id:
        return ResolvedMedia("link", url)

    requester = session or requests.Session()
    should_close = session is None
    try:
        metadata_url = (
            IMGUR_ALBUM_IMAGES_API.format(media_id=album_id)
            if album_id
            else IMGUR_IMAGE_API.format(media_id=media_id)
        )
        response = requester.get(
            metadata_url,
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
    if album_id and isinstance(data, list):
        for item in data:
            if resolved := _resolved_imgur_item(item):
                return resolved
    elif resolved := _resolved_imgur_item(data):
        return resolved
    return ResolvedMedia("link", url)


@lru_cache(maxsize=512)
def resolve_media_url_cached(url: str) -> ResolvedMedia:
    return resolve_media_url(url)


def open_media_stream(
    url: str,
    *,
    range_header: str | None = None,
    session: requests.Session | None = None,
) -> requests.Response:
    """Open one allowlisted media asset and preserve browser Range requests."""

    if _safe_proxied_asset(url) is None:
        raise MediaUpstreamError("Media URL is not an allowlisted asset")

    headers = {
        "Accept": "image/*,video/*",
        "User-Agent": "hanni-media-proxy/1.0",
    }
    if range_header and re.fullmatch(r"bytes=\d*-\d*", range_header):
        headers["Range"] = range_header

    requester = session or requests
    response: requests.Response | None = None
    for attempt in range(2):
        try:
            response = requester.get(url, headers=headers, timeout=(5, 30), stream=True)
        except requests.RequestException as error:
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise MediaUpstreamError("Upstream media request failed") from error

        if response.status_code in {200, 206}:
            break
        status_code = response.status_code
        response.close()
        response = None
        if attempt == 0 and status_code in TRANSIENT_UPSTREAM_STATUSES:
            time.sleep(0.5)
            continue
        raise MediaUpstreamError(f"Upstream host returned HTTP {status_code}")

    if response is None:
        raise MediaUpstreamError("Upstream media request failed")
    if _safe_proxied_asset(response.url) is None:
        response.close()
        raise MediaUpstreamError("Media redirected to a disallowed host")

    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if not (content_type.startswith("image/") or content_type.startswith("video/")):
        response.close()
        raise MediaUpstreamError("Upstream host returned a non-media response")
    return response
