"""Concurrency tests for SecretModel: cold-cache thundering herd, racing
refresh vs fetch, etc."""

from __future__ import annotations

import threading
import time

from vaultly import Secret, SecretModel
from vaultly.backends.base import Backend


class SlowBackend(Backend):
    """Sleeps `delay` seconds inside `get` so we can race goroutines."""

    def __init__(self, value: str = "v", delay: float = 0.1) -> None:
        self.value = value
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def get(self, path: str, *, version: int | str | None = None) -> str:
        del path, version
        with self._lock:
            self.calls += 1
        time.sleep(self.delay)
        return self.value


class App(SecretModel):
    api_key: str = Secret("/api")


def test_concurrent_cold_fetch_makes_one_backend_call():
    """50 threads racing on cold cache — backend hit exactly once."""
    backend = SlowBackend(value="sk", delay=0.1)
    c = App(backend=backend)

    results: list[str] = []
    barrier = threading.Barrier(50)

    def worker() -> None:
        barrier.wait()
        results.append(c.api_key)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert backend.calls == 1
    assert results == ["sk"] * 50


def test_concurrent_warm_cache_no_lock_contention():
    """Once cached, hot reads don't take the per-key lock — verify by timing."""
    backend = SlowBackend(value="v", delay=0)
    c = App(backend=backend)
    _ = c.api_key  # warm

    start = time.monotonic()
    for _ in range(10_000):
        _ = c.api_key
    elapsed = time.monotonic() - start

    # 10k reads should finish in well under a second; this is a smoke
    # threshold, not a tight benchmark.
    assert elapsed < 1.0
    assert backend.calls == 1
