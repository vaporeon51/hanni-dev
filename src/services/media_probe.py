"""HTTP media liveness checks used by the dead-link worker.

The probe deliberately does not use Discord unfurls. It checks the actual
allowlisted media URL, follows redirects, reads only a tiny response prefix,
and treats transient failures as unknown rather than dead.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import requests

from src.config.constants import MEDIA_ALLOWED_HOSTS, DEAD_LINK_REQUEST_TIMEOUT_SECONDS

ProbeStatus = Literal["live", "dead", "unknown"]
_MEDIA_CONTENT_TYPES = ("image/", "video/", "audio/")
_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".mp4", ".webm", ".mov"}
_IMGUR_HOSTS = {"imgur.com", "www.imgur.com", "i.imgur.com"}
_IMGUR_ID_RE = re.compile(r"^[A-Za-z0-9]+$")


@dataclass(frozen=True)
class ProbeResult:
    url: str
    status: ProbeStatus
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    error: str | None = None


def _host_allowed(host: str | None) -> bool:
    return bool(host) and host.lower().rstrip(".") in MEDIA_ALLOWED_HOSTS


def _imgur_candidates(url: str) -> list[str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _IMGUR_HOSTS:
        return [url]
    parts = [part for part in parsed.path.split("/") if part]
    if host == "i.imgur.com":
        return [url]
    if not parts or not _IMGUR_ID_RE.fullmatch(parts[-1].split(".", 1)[0]):
        return [url]
    imgur_id = parts[-1].split(".", 1)[0]
    return [f"https://i.imgur.com/{imgur_id}{extension}" for extension in (".mp4", ".webm", ".gif", ".jpg", ".png", ".jpeg", ".webp")]


def _looks_like_media(*, url: str, content_type: str, prefix: bytes) -> bool:
    if content_type.startswith(_MEDIA_CONTENT_TYPES):
        return True
    path = urlparse(url).path.lower()
    if any(path.endswith(extension) for extension in _MEDIA_EXTENSIONS):
        return prefix.startswith(
            (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"\x00\x00\x00", b"\x1a\x45\xdf\xa3")
        )
    return prefix.startswith(
        (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"\x00\x00\x00", b"\x1a\x45\xdf\xa3")
    )


def _is_deleted_imgur_placeholder(*, content_type: str, content_length: int | None, prefix: bytes) -> bool:
    # Imgur has historically returned a tiny placeholder image for deleted
    # assets. The dimensions/bytes vary, so size alone is not sufficient; the
    # known placeholder's small PNG signature plus 503-byte length is used as
    # a conservative extra check.
    if not (content_type.startswith("image/png") and content_length == 503 and prefix.startswith(b"\x89PNG")):
        return False
    if len(prefix) < 24:
        return True
    return struct.unpack(">II", prefix[16:24]) == (161, 81)


def _probe_one(session: requests.Session, url: str, timeout: int) -> ProbeResult:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not _host_allowed(parsed.hostname):
            return ProbeResult(url=url, status="unknown", error="host or scheme is not allowlisted")

        response = session.get(
            url,
            allow_redirects=True,
            stream=True,
            headers={"Range": "bytes=0-1023", "User-Agent": "hanni-link-checker/1.0"},
            timeout=(10, timeout),
        )
        try:
            final_url = response.url
            final_host = urlparse(final_url).hostname
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            content_length_header = response.headers.get("Content-Length")
            try:
                content_length = int(content_length_header) if content_length_header else None
            except ValueError:
                content_length = None
            prefix = next(response.iter_content(chunk_size=128), b"")
            if not _host_allowed(final_host):
                return ProbeResult(url=url, status="unknown", final_url=final_url, status_code=response.status_code, error="redirected to a non-allowlisted host")
            if response.status_code in {404, 410}:
                return ProbeResult(url=url, status="dead", final_url=final_url, status_code=response.status_code, content_type=content_type, error=f"HTTP {response.status_code}")
            if response.status_code == 429 or response.status_code >= 500:
                return ProbeResult(url=url, status="unknown", final_url=final_url, status_code=response.status_code, content_type=content_type, error=f"transient HTTP {response.status_code}")
            if not 200 <= response.status_code < 300:
                return ProbeResult(url=url, status="unknown", final_url=final_url, status_code=response.status_code, content_type=content_type, error=f"HTTP {response.status_code}")
            if _is_deleted_imgur_placeholder(content_type=content_type, content_length=content_length, prefix=prefix):
                return ProbeResult(url=url, status="dead", final_url=final_url, status_code=response.status_code, content_type=content_type, error="deleted media placeholder")
            if _looks_like_media(url=final_url, content_type=content_type, prefix=prefix):
                return ProbeResult(url=url, status="live", final_url=final_url, status_code=response.status_code, content_type=content_type)
            if parsed.hostname and parsed.hostname.lower().rstrip(".") not in _IMGUR_HOSTS and content_type.startswith("text/html"):
                return ProbeResult(url=url, status="live", final_url=final_url, status_code=response.status_code, content_type=content_type)
            return ProbeResult(url=url, status="unknown", final_url=final_url, status_code=response.status_code, content_type=content_type, error="successful response was not media")
        finally:
            response.close()
    except requests.Timeout as exc:
        return ProbeResult(url=url, status="unknown", error=f"timeout: {exc}")
    except requests.RequestException as exc:
        return ProbeResult(url=url, status="unknown", error=f"request error: {exc}")
    except Exception as exc:  # pragma: no cover - defensive boundary for worker safety
        return ProbeResult(url=url, status="unknown", error=f"probe error: {exc}")


def probe_url(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout: int = DEAD_LINK_REQUEST_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Probe one media URL, including the direct variants of an Imgur page."""

    owns_session = session is None
    session = session or requests.Session()
    try:
        candidates = _imgur_candidates(url)
        results = [_probe_one(session, candidate, timeout) for candidate in candidates]
        for result in results:
            if result.status == "live":
                return ProbeResult(url=url, status="live", final_url=result.final_url, status_code=result.status_code, content_type=result.content_type)
        if any(result.status == "unknown" for result in results):
            first_unknown = next(result for result in results if result.status == "unknown")
            return ProbeResult(url=url, status="unknown", final_url=first_unknown.final_url, status_code=first_unknown.status_code, content_type=first_unknown.content_type, error=first_unknown.error)
        first_dead = results[0]
        return ProbeResult(url=url, status="dead", final_url=first_dead.final_url, status_code=first_dead.status_code, content_type=first_dead.content_type, error=first_dead.error)
    finally:
        if owns_session:
            session.close()
