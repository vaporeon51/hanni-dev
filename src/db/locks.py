"""Small Postgres advisory-lock helper for safe one-off or scaled workers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from src.db import POOL


@contextmanager
def advisory_lock(name: str) -> Iterator[bool]:
    """Yield whether this process acquired a session-level named lock."""

    with POOL.connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (name,))
        locked = bool(cursor.fetchone()[0])
        try:
            yield locked
        finally:
            if locked:
                cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (name,))
