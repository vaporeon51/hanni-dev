"""Resolve a stored content URL to one browser-ready media asset."""

from __future__ import annotations

import html
import os
import re
import threading
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
PROXIED_MEDIA_HOSTS = IMGUR_HOSTS | {"cdn.goyangi.pics", "cdn.kpopping.com", "i.imgur.gg"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
TRANSIENT_UPSTREAM_STATUSES = {429, 502, 503, 504}

_IMGUR_TRANSIENT_TTL_SECONDS = 120.0
_IMGUR_COOLDOWN_MAX_SECONDS = 6 * 60 * 60
_IMGUR_TRANSIENT_CAP = 2048

_state_lock = threading.Lock()
_shared_session_instance: requests.Session | None = None
_transient_errors: dict[str, tuple[float, MediaResolutionError]] = {}
_imgur_cooldown_until: float = 0.0


def _shared_session() -> requests.Session:
    """Reuse one connection-pooled session for production metadata lookups."""

    global _shared_session_instance
    with _state_lock:
        if _shared_session_instance is None:
            _shared_session_instance = requests.Session()
            _shared_session_instance.headers.update({"User-Agent": "hanni-web/1.0"})
        return _shared_session_instance


def _transient_error(url: str) -> MediaResolutionError | None:
    now = time.monotonic()
    with _state_lock:
        entry = _transient_errors.get(url)
        if entry is None:
            return None
        expires_at, error = entry
        if expires_at <= now:
            del _transient_errors[url]
            return None
        return error


def _note_transient_error(url: str, error: MediaResolutionError) -> None:
    with _state_lock:
        if len(_transient_errors) >= _IMGUR_TRANSIENT_CAP:
            now = time.monotonic()
            for key, (expires_at, _) in list(_transient_errors.items()):
                if expires_at <= now:
                    del _transient_errors[key]
                if len(_transient_errors) < _IMGUR_TRANSIENT_CAP:
                    break
        _transient_errors[url] = (time.monotonic() + _IMGUR_TRANSIENT_TTL_SECONDS, error)


def _imgur_cooldown_remaining() -> float:
    with _state_lock:
        return max(0.0, _imgur_cooldown_until - time.monotonic())


def _note_imgur_rate_limit(response: requests.Response) -> None:
    """Pause all metadata lookups when Imgur reports an exhausted quota."""

    if response.status_code != 429:
        return
    headers = response.headers or {}
    try:
        remaining = int(headers.get("X-RateLimit-ClientRemaining", ""))
    except (TypeError, ValueError):
        remaining = None
    try:
        reset = float(headers.get("X-RateLimit-ClientReset", ""))
    except (TypeError, ValueError):
        reset = 0.0
    if remaining == 0 and reset > 0:
        delay = min(reset, _IMGUR_COOLDOWN_MAX_SECONDS)
    else:
        delay = 30.0
    global _imgur_cooldown_until
    with _state_lock:
        _imgur_cooldown_until = max(_imgur_cooldown_until, time.monotonic() + delay)


class MediaUpstreamError(RuntimeError):
    """An allowlisted host did not return a browser-playable media response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int = 2,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class MediaResolutionError(RuntimeError):
    """Imgur metadata could not be resolved because of a transient failure."""

    def __init__(self, message: str, *, retry_after_seconds: int = 3) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


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


def _retry_after_seconds(response: requests.Response, default: int) -> int:
    try:
        return max(1, min(30, int(float(response.headers.get("Retry-After", default)))))
    except (TypeError, ValueError):
        return default


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

    if (urlsplit(url).hostname or "").lower() == GOYANGI_PAGE_HOST:
        resolved_page = _resolve_goyangi_page(url, session=session)
        return resolved_page if resolved_page is not None else ResolvedMedia("link", url)

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
    if media_id and not album_id and session is None:
        probed = _probe_imgur_direct(media_id)
        if probed is not None:
            return probed
    if not (media_id or album_id) or not resolved_client_id:
        return ResolvedMedia("link", url)

    managed = session is None
    if managed:
        cached = _transient_error(url)
        if cached is not None:
            raise cached
        cooldown = _imgur_cooldown_remaining()
        if cooldown > 0:
            og_resolved = _resolve_imgur_page_og(url)
            if og_resolved is not None:
                return og_resolved
            raise MediaResolutionError(
                "Imgur metadata lookups are cooling down",
                retry_after_seconds=int(min(cooldown, _IMGUR_COOLDOWN_MAX_SECONDS)),
            )
    requester = session if session is not None else _shared_session()
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
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            if response.status_code in TRANSIENT_UPSTREAM_STATUSES:
                failure = MediaResolutionError(
                    f"Imgur metadata temporarily returned HTTP {response.status_code}",
                    retry_after_seconds=_retry_after_seconds(response, 3),
                )
                if managed:
                    _note_imgur_rate_limit(response)
                    og_resolved = _resolve_imgur_page_og(url)
                    if og_resolved is not None:
                        return og_resolved
                    _note_transient_error(url, failure)
                raise failure from error
            return ResolvedMedia("link", url)
        payload = response.json()
    except MediaResolutionError:
        raise
    except requests.RequestException as error:
        failure = MediaResolutionError("Imgur metadata request temporarily failed")
        if managed:
            og_resolved = _resolve_imgur_page_og(url)
            if og_resolved is not None:
                return og_resolved
            _note_transient_error(url, failure)
        raise failure from error
    except ValueError:
        return ResolvedMedia("link", url)

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
        retry_after_seconds = _retry_after_seconds(response, 2)
        response.close()
        response = None
        if attempt == 0 and status_code in TRANSIENT_UPSTREAM_STATUSES:
            time.sleep(0.5)
            continue
        raise MediaUpstreamError(
            f"Upstream host returned HTTP {status_code}",
            status_code=status_code,
            retry_after_seconds=retry_after_seconds,
        )

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


_IMGUR_PROBE_EXTENSIONS = (("mp4", "video"), ("jpg", "image"), ("png", "image"), ("gif", "image"))
_IMGUR_PROBE_TIMEOUT = (3, 5)


def _probe_imgur_direct(media_id: str) -> ResolvedMedia | None:
    """Map a single-image page ID to its file with keyless CDN HEADs.

    Page IDs double as file IDs, so a direct fetch needs no API quota.
    Order matters: only take an image when no video exists, because video
    IDs also serve small poster JPEGs. Albums never reach here.
    """

    requester = _shared_session()
    for extension, kind in _IMGUR_PROBE_EXTENSIONS:
        candidate = f"https://i.imgur.com/{media_id}.{extension}"
        try:
            response = requester.head(candidate, timeout=_IMGUR_PROBE_TIMEOUT)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            continue
        content_type = (response.headers.get("Content-Type", "") or "").lower()
        if kind == "video" and not content_type.startswith("video/"):
            continue
        if kind == "image" and not content_type.startswith("image/"):
            continue
        return ResolvedMedia(kind, candidate)
    return None


GOYANGI_PAGE_HOST = "goyangi.pics"
GOYANGI_REFRESH_PATTERN = re.compile(
    r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]*content=[\"']?\d+\s*;\s*url=([^\"'\s>]+)",
    re.IGNORECASE,
)


def _resolve_goyangi_page(url: str, *, session: requests.Session | None = None) -> ResolvedMedia | None:
    """Follow a goyangi viewer page to its file without any API quota.

    Viewer pages are tiny meta-refresh stubs whose filenames morph, so only
    the redirect target is usable. Anything off-allowlist stays a plain link.
    """

    requester = session if session is not None else _shared_session()
    try:
        response = requester.get(url, timeout=(5, 12))
    except requests.RequestException:
        return None
    final = _safe_proxied_asset(response.url)
    if final is not None and final != url:
        target: str | None = final
    else:
        text = getattr(response, "text", "") or ""
        match = GOYANGI_REFRESH_PATTERN.search(text[:32768])
        if not match:
            return None
        target = _safe_proxied_asset(html.unescape(match.group(1)))
        if target is None:
            return None
    extension = _extension(target)
    if extension in VIDEO_EXTENSIONS:
        return ResolvedMedia("video", target)
    if extension in IMAGE_EXTENSIONS:
        return ResolvedMedia("image", target)
    return None


OG_VIDEO_PATTERN = re.compile(
    r"<meta[^>]+property=[\"']og:video[\"'][^>]*content=[\"']([^\"']+)",
    re.IGNORECASE,
)
OG_IMAGE_PATTERN = re.compile(
    r"<meta[^>]+property=[\"']og:image[\"'][^>]*content=[\"']([^\"']+)",
    re.IGNORECASE,
)
OG_PAGE_SCAN_LIMIT = 131072


def _resolve_imgur_page_og(url: str) -> ResolvedMedia | None:
    """Scrape Open Graph tags as a last resort when the API quota is dead.

    Unfurl tags must stay correct for Discord/Twitter previews, which makes
    them stabler than internal markup. Yields the first attachment only.
    """

    try:
        response = _shared_session().get(url, timeout=(5, 12))
    except requests.RequestException:
        return None
    if getattr(response, "status_code", 200) != 200:
        return None
    text = getattr(response, "text", "") or ""
    for pattern, kind in ((OG_VIDEO_PATTERN, "video"), (OG_IMAGE_PATTERN, "image")):
        match = pattern.search(text[:OG_PAGE_SCAN_LIMIT])
        if not match:
            continue
        asset = _safe_imgur_asset(html.unescape(match.group(1)))
        if asset is not None:
            return ResolvedMedia(kind, asset)
    return None
