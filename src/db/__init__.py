"""Lazy Postgres connection-pool lifecycle shared by the web app and worker."""

from __future__ import annotations

import os
from typing import Any

from psycopg_pool import ConnectionPool


class ManagedPool:
    """Create the pool only when an application process explicitly opens it."""

    def __init__(self) -> None:
        self._pool: ConnectionPool[Any] | None = None

    def open(self) -> None:
        if self._pool is not None:
            return
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        min_size = max(1, int(os.getenv("DB_POOL_MIN_SIZE", "1")))
        max_size = max(min_size, int(os.getenv("DB_POOL_MAX_SIZE", "5")))
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            open=False,
        )
        self._pool.open(wait=True)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def connection(self):
        if self._pool is None:
            raise RuntimeError("Database pool is not open; call POOL.open() during application startup")
        return self._pool.connection()


POOL = ManagedPool()

