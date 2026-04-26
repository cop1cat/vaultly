"""Regression tests for issues found in the second review pass.

#3: refresh() must be atomic — concurrent _fetch can't slip in between
    invalidate and the new fetch and leave the cache populated with a value
    older than the refresh-trigger expected.
#5: subclassing a SecretModel preserves cast types — the subclass must not
    end up using the wrapped Annotated[SkipValidation[T], ...] as the cast
    target.
#6: prefetch deduplicates versioned entries.
"""

from __future__ import annotations

import threading
import time

from vaultly import Secret, SecretModel
from vaultly.backends.base import Backend
from vaultly.testing.mock import MockBackend

# --------------------------------------------------------------------------- #5


def test_subclass_preserves_int_cast():
    class Base(SecretModel):
        n: int = Secret("/n")

    class Child(Base):
        pass

    c = Child(backend=MockBackend({"/n": "42"}))
    assert c.n == 42
    assert isinstance(c.n, int)


def test_subclass_preserves_dict_cast():
    class Base(SecretModel):
        flags: dict = Secret("/flags")

    class Child(Base):
        pass

    c = Child(backend=MockBackend({"/flags": '{"a": 1}'}))
    assert c.flags == {"a": 1}
    assert isinstance(c.flags, dict)


def test_subclass_preserves_bool_cast():
    class Base(SecretModel):
        debug: bool = Secret("/debug")

    class Child(Base):
        pass

    c = Child(backend=MockBackend({"/debug": "true"}))
    assert c.debug is True


def test_subclass_adds_own_secret():
    class Base(SecretModel):
        x: int = Secret("/x")

    class Child(Base):
        y: float = Secret("/y")

    c = Child(backend=MockBackend({"/x": "1", "/y": "2.5"}))
    assert c.x == 1
    assert c.y == 2.5


# --------------------------------------------------------------------------- #3


class CountingBackend(Backend):
    """Returns sequential values: '1', '2', '3', ... — every call is unique."""

    def __init__(self, delay: float = 0.0) -> None:
        self.calls = 0
        self.delay = delay
        self._lock = threading.Lock()

    def get(self, path: str, *, version: int | str | None = None) -> str:
        del path, version
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.calls += 1
            return str(self.calls)


class App(SecretModel):
    token: str = Secret("/t")


def test_refresh_actually_calls_backend_under_concurrent_fetch():
    """A reader holding the cache-miss path must not let `refresh` skip
    the backend call. After refresh returns, the backend must have been
    called at least once *for the refresh*, not just for the racing read."""
    backend = CountingBackend(delay=0.05)
    c = App(backend=backend)

    # Warm the cache.
    assert c.token == "1"
    assert backend.calls == 1

    # Concurrent reader hitting cache (already warm) and refresher.
    refresher_value: list[str] = []
    reader_value: list[str] = []

    def refresher() -> None:
        refresher_value.append(c.refresh("token"))

    def reader() -> None:
        reader_value.append(c.token)

    t1 = threading.Thread(target=refresher)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Backend was called exactly twice: once initially, once for refresh.
    assert backend.calls == 2
    # Refresher saw the new value.
    assert refresher_value == ["2"]


def test_concurrent_refresh_calls_backend_each_time():
    """N threads all calling refresh — each must observe its own backend
    call (refresh is *not* coalesced, that's invalidate-then-fetch under
    the same lock)."""
    backend = CountingBackend(delay=0.01)
    c = App(backend=backend)
    _ = c.token  # warm
    backend.calls = 0  # reset for clarity

    results: list[str] = []
    barrier = threading.Barrier(10)

    def worker() -> None:
        barrier.wait()
        results.append(c.refresh("token"))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every refresh hits the backend.
    assert backend.calls == 10
    # All results are distinct sequential values.
    assert sorted(results) == sorted(str(i) for i in range(1, 11))


# --------------------------------------------------------------------------- #6


def test_prefetch_dedups_versioned_paths():
    """Two fields pointing at the same path+version share one backend call."""

    class M(SecretModel):
        a: str = Secret("/x", version=1)
        b: str = Secret("/x", version=1)

    b = MockBackend(versions={("/x", 1): "v"})
    c = M(backend=b)
    b.reset_calls()
    c.prefetch()
    # Without dedup we'd see two ("/x", 1) entries.
    assert b.calls == [("/x", 1)]


# --------------------------------------------------------------------------- #7


def test_keyed_locks_discard_releases_unheld_lock():
    from vaultly.core.cache import KeyedLocks

    locks = KeyedLocks()
    lock = locks.for_key("/x")
    locks.discard("/x")
    # New call returns a fresh lock (not the discarded one).
    new_lock = locks.for_key("/x")
    assert new_lock is not lock


def test_keyed_locks_discard_skips_held_lock():
    """`discard` from one thread must refuse to drop a lock held by another."""
    from vaultly.core.cache import KeyedLocks

    locks = KeyedLocks()
    lock = locks.for_key("/x")

    holder_acquired = threading.Event()
    holder_release = threading.Event()

    def hold() -> None:
        lock.acquire()
        try:
            holder_acquired.set()
            holder_release.wait(timeout=2.0)
        finally:
            lock.release()

    t = threading.Thread(target=hold)
    t.start()
    holder_acquired.wait(timeout=2.0)
    try:
        locks.discard("/x")  # called from main thread; lock held by t
        assert locks.for_key("/x") is lock  # not dropped
    finally:
        holder_release.set()
        t.join()


def test_keyed_locks_clear():
    from vaultly.core.cache import KeyedLocks

    locks = KeyedLocks()
    a = locks.for_key("/a")
    b = locks.for_key("/b")
    locks.clear()
    assert locks.for_key("/a") is not a
    assert locks.for_key("/b") is not b
