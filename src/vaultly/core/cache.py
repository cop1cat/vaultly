"""Thread-safe TTL cache keyed by resolved secret path.

Also exposes `KeyedLocks` — a small helper that hands out a distinct
`threading.Lock` per cache key, so concurrent fetches of the same secret
on cold cache do not stampede the backend.

TTL semantics:
  * `ttl=None`   — entry never expires.
  * `ttl=0`      — entry is stored with `expires == now`, so the next `get`
                   treats it as expired and misses. Equivalent to "no cache".
  * `ttl > 0`    — entry expires `ttl` seconds after insertion.

`get` and `peek_expired` differ only in how they treat expired entries: the
former raises `KeyError` and evicts; the latter returns the stale value (used
by `stale_on_error`).
"""

from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float | None]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        """Return a fresh value or raise `KeyError` (miss or expired).

        Expired entries are *not* evicted here — they remain accessible via
        `peek_expired`, which is what `stale_on_error` uses. They are
        overwritten by the next successful `set`, or removed explicitly by
        `invalidate` / `clear`.
        """
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                raise KeyError(key)
            value, expires = entry
            if expires is not None and time.monotonic() >= expires:
                raise KeyError(key)
            return value

    def set(self, key: str, value: Any, ttl: float | None) -> None:
        expires = None if ttl is None else time.monotonic() + ttl
        with self._lock:
            self._data[key] = (value, expires)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def peek_expired(self, key: str) -> Any:
        """Return the value even if expired; `KeyError` only if absent."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                raise KeyError(key)
            return entry[0]


class KeyedLocks:
    """One `threading.Lock` per key. Hands the same lock back for repeat keys.

    Used to serialize concurrent backend fetches of the same secret on cold
    cache (the "thundering herd" — N readers hit the backend N times instead
    of once). Locks accumulate forever; for paths bounded by model shape this
    is fine.
    """

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def for_key(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock
