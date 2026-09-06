"""Bounded process-local history for avoiding repetitive random feeds."""

from __future__ import annotations

from collections import OrderedDict, deque
from threading import Lock


class RecentFeedHistory:
    """Keep a small URL queue for each anonymous browser visitor."""

    def __init__(self, *, per_visitor_capacity: int = 50, visitor_capacity: int = 1024) -> None:
        if per_visitor_capacity < 1 or visitor_capacity < 1:
            raise ValueError("feed history capacities must be at least 1")
        self.per_visitor_capacity = per_visitor_capacity
        self.visitor_capacity = visitor_capacity
        self._queues: OrderedDict[str, deque[str]] = OrderedDict()
        self._lock = Lock()

    def recent_urls(self, visitor_id: str) -> tuple[str, ...]:
        with self._lock:
            queue = self._queues.get(visitor_id)
            if queue is None:
                return ()
            self._queues.move_to_end(visitor_id)
            return tuple(queue)

    def remember(self, visitor_id: str, url: str) -> None:
        with self._lock:
            queue = self._queues.get(visitor_id)
            if queue is None:
                queue = deque(maxlen=self.per_visitor_capacity)
                self._queues[visitor_id] = queue
            elif url in queue:
                queue.remove(url)
            queue.append(url)
            self._queues.move_to_end(visitor_id)
            while len(self._queues) > self.visitor_capacity:
                self._queues.popitem(last=False)

    def clear(self, visitor_id: str) -> None:
        with self._lock:
            self._queues.pop(visitor_id, None)


feed_history = RecentFeedHistory()
scroll_history = RecentFeedHistory(per_visitor_capacity=100)
link_history = RecentFeedHistory(per_visitor_capacity=100)
