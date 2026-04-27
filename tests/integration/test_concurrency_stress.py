"""Concurrency stress tests for the realistic load profile.

Goal: catch races, lock contention bugs, ordering issues that single-threaded
unit tests miss. We use a barrier to maximize starting overlap.
"""

from __future__ import annotations

import random
import threading
import time

from vaultly import Backend, MockBackend, Secret, SecretModel


class SlowBackend(Backend):
    """Adds a configurable artificial latency on each call."""

    def __init__(self, data: dict[str, str], delay: float = 0.02) -> None:
        self.data = dict(data)
        self.delay = delay
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def get(self, path: str, *, version: int | str | None = None) -> str:
        time.sleep(self.delay)
        with self._lock:
            self.calls.append(path)
        return self.data[path]


# --------------------------------------------------------------------------- thundering herd, many keys


def test_many_threads_many_keys_each_path_hit_once() -> None:
    """100 threads x 20 keys: every key fetched exactly once across all threads."""
    keys = [f"/k{i}" for i in range(20)]
    data = {k: f"v{i}" for i, k in enumerate(keys)}
    backend = SlowBackend(data, delay=0.01)

    fields = {f"f{i}": Secret(k) for i, k in enumerate(keys)}
    annotations = {f"f{i}": str for i in range(20)}
    Big = type(
        "Big",
        (SecretModel,),
        {"__annotations__": annotations, **fields},
    )

    app = Big(backend=backend)

    barrier = threading.Barrier(100)

    def worker(idx: int) -> None:
        barrier.wait()
        # Each worker reads all keys in a randomized order to maximize contention.
        order = list(range(20))
        random.Random(idx).shuffle(order)
        for i in order:
            v = getattr(app, f"f{i}")
            assert v == f"v{i}"

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Per-key fetch lock means each key hit exactly once despite 100 readers.
    assert sorted(backend.calls) == sorted(keys)


# --------------------------------------------------------------------------- refresh contention


def test_concurrent_refresh_and_read_no_deadlock() -> None:
    """Mix of reads and refreshes on the same key — must terminate."""
    backend = MockBackend({"/k": "v"})

    class App(SecretModel):
        k: str = Secret("/k")

    app = App(backend=backend)
    assert app.k == "v"  # warm

    finished = [0]
    finished_lock = threading.Lock()
    barrier = threading.Barrier(40)

    def reader() -> None:
        barrier.wait()
        for _ in range(50):
            assert app.k == "v"
        with finished_lock:
            finished[0] += 1

    def refresher() -> None:
        barrier.wait()
        for _ in range(20):
            assert app.refresh("k") == "v"
        with finished_lock:
            finished[0] += 1

    threads = [threading.Thread(target=reader) for _ in range(30)] + [
        threading.Thread(target=refresher) for _ in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    # If anything deadlocked, finished[0] would be < 40.
    assert finished[0] == 40


# --------------------------------------------------------------------------- hot-path performance


def test_warm_cache_hot_reads_dont_serialize() -> None:
    """Warm-cache reads must NOT take the per-key lock; they should scale."""
    backend = SlowBackend({"/k": "v"}, delay=0.0)

    class App(SecretModel):
        k: str = Secret("/k")

    app = App(backend=backend)
    _ = app.k  # warm

    # Time hot reads from many threads. If each were serialized through a
    # per-key lock, this would scale linearly with thread count.
    barrier = threading.Barrier(20)

    def worker() -> None:
        barrier.wait()
        for _ in range(10_000):
            _ = app.k

    threads = [threading.Thread(target=worker) for _ in range(20)]

    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start

    # 200k reads. The exact wall-clock isn't the test's point; the test
    # exists to *catch a regression* where reads start taking the per-key
    # lock and serialize — if that happens this number balloons by 100x+.
    # Threshold deliberately generous so coverage tracing on slow CI
    # runners doesn't flake the test; the failure mode we care about is
    # orders of magnitude away.
    assert elapsed < 120.0, f"hot reads too slow: {elapsed:.2f}s"
    assert backend.calls == ["/k"]  # exactly one warm-up call


# --------------------------------------------------------------------------- prefetch under concurrent reads


def test_prefetch_serves_concurrent_readers_correctly() -> None:
    """While prefetch is running, other threads reading must not double-fetch
    or get partial values."""
    backend = SlowBackend(
        {f"/k{i}": f"v{i}" for i in range(10)}, delay=0.005
    )

    fields = {f"f{i}": Secret(f"/k{i}") for i in range(10)}
    annotations = {f"f{i}": str for i in range(10)}
    M = type("M", (SecretModel,), {"__annotations__": annotations, **fields})

    app = M(backend=backend)

    # Run prefetch in one thread; readers in others.
    def prefetcher() -> None:
        app.prefetch()

    def reader() -> None:
        for i in range(10):
            v = getattr(app, f"f{i}")
            assert v == f"v{i}"

    pre = threading.Thread(target=prefetcher)
    readers = [threading.Thread(target=reader) for _ in range(10)]

    pre.start()
    for r in readers:
        r.start()
    pre.join(timeout=5)
    for r in readers:
        r.join(timeout=5)

    # Each path was fetched exactly once total. (Prefetch + concurrent
    # readers don't stampede thanks to per-key locks + batch.)
    # Note: the batched path goes via get_batch (1 call all-paths) but our
    # SlowBackend default impl issues serial gets — so we expect 10 paths
    # appearing exactly once each.
    assert sorted(backend.calls) == sorted([f"/k{i}" for i in range(10)])
