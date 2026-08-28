"""Process-local priority queue for URLs actually revealed in the web feed."""

from __future__ import annotations

from collections import deque
from threading import Lock

DEAD_LINK_PRIORITY_QUEUE_SIZE = 100


class BoundedUrlQueue:
    """Thread-safe, de-duplicated FIFO retaining the newest bounded workload."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("Queue capacity must be positive")
        self.capacity = int(capacity)
        self._urls: deque[str] = deque()
        self._pending: set[str] = set()
        self._lock = Lock()

    def enqueue(self, url: str) -> bool:
        if not isinstance(url, str) or not url:
            return False
        with self._lock:
            if url in self._pending:
                return False
            if len(self._urls) >= self.capacity:
                self._pending.remove(self._urls.popleft())
            self._urls.append(url)
            self._pending.add(url)
            return True

    def take(self, limit: int) -> list[str]:
        taken: list[str] = []
        with self._lock:
            for _ in range(min(max(0, int(limit)), len(self._urls))):
                url = self._urls.popleft()
                self._pending.remove(url)
                taken.append(url)
        return taken

    def __len__(self) -> int:
        with self._lock:
            return len(self._urls)


_PRIORITY_URLS = BoundedUrlQueue(DEAD_LINK_PRIORITY_QUEUE_SIZE)


def enqueue_priority_url(url: str) -> bool:
    return _PRIORITY_URLS.enqueue(url)


def take_priority_urls(limit: int) -> list[str]:
    return _PRIORITY_URLS.take(limit)


def priority_url_count() -> int:
    return len(_PRIORITY_URLS)
