"""Realistic multi-component stacks: RetryingBackend + stale_on_error +
nested models + rotation, with fault-injection at the inner backend."""

from __future__ import annotations

import threading
import time

import pytest

from vaultly import (
    Backend,
    MockBackend,
    RetryingBackend,
    Secret,
    SecretModel,
    TransientError,
)

# --------------------------------------------------------------------------- fault-injectable backend


class FaultyBackend(Backend):
    """In-memory KV with externally-controlled fault injection.

    `transient_fail_count` — drop the next N calls with TransientError.
    `auth_fail` — raise AuthError on every call until cleared.
    """

    def __init__(self, data: dict[str, str]) -> None:
        self.data = dict(data)
        self.calls: list[str] = []
        self.transient_fail_count = 0
        self.auth_fail = False
        self._lock = threading.Lock()

    def get(self, path: str, *, version: int | str | None = None) -> str:
        with self._lock:
            self.calls.append(path)
            if self.auth_fail:
                from vaultly.errors import AuthError
                raise AuthError(f"denied: {path}")
            if self.transient_fail_count > 0:
                self.transient_fail_count -= 1
                raise TransientError(f"flap: {path}")
            return self.data[path]


# --------------------------------------------------------------------------- realistic stack


class DbConfig(SecretModel):
    password: str = Secret("/{stage}/db/password", ttl=0.05)


class App(SecretModel, stale_on_error=True):
    stage: str
    db: DbConfig
    api_key: str = Secret("/{stage}/api/key", ttl=0.05)


def _build_app(stage: str = "prod") -> tuple[FaultyBackend, App]:
    inner = FaultyBackend(
        {
            "/prod/db/password": "pw1",
            "/prod/api/key": "sk1",
        }
    )
    backend = RetryingBackend(
        inner,
        max_attempts=4,
        base_delay=0.001,
        max_delay=0.01,
        jitter=False,
        sleep=lambda _d: None,
    )
    app = App(stage=stage, db={}, backend=backend)
    return inner, app


def test_retry_recovers_from_transient_failures() -> None:
    inner, app = _build_app()
    # First 2 calls flake; retry layer hides this from us.
    inner.transient_fail_count = 2
    assert app.db.password == "pw1"
    # Underlying backend was called 3 times (2 fails + 1 success); the
    # vaultly-level reader saw exactly one logical fetch.
    assert inner.calls.count("/prod/db/password") == 3


def test_retry_total_failure_falls_back_to_stale_value() -> None:
    inner, app = _build_app()
    # Warm cache.
    assert app.db.password == "pw1"
    # Wait for TTL to expire.
    time.sleep(0.06)
    # Now everything fails — exhausts retry budget then surfaces TransientError
    # to vaultly's _do_fetch, which falls back to the expired cache value.
    inner.transient_fail_count = 100  # always fail
    assert app.db.password == "pw1"  # stale-on-error fallback


def test_auth_error_short_circuits_retry_and_fetch() -> None:
    inner, app = _build_app()
    inner.auth_fail = True
    from vaultly.errors import AuthError
    with pytest.raises(AuthError):
        _ = app.api_key
    # AuthError is not retried.
    assert inner.calls.count("/prod/api/key") == 1


def test_concurrent_readers_during_partial_outage() -> None:
    """50 threads racing to read while the backend is intermittently flaking.
    The combination of (per-key lock + retry + stale_on_error) must produce
    correct values without raising."""
    inner, app = _build_app()
    # Warm cache so stale_on_error has something to fall back to.
    _ = app.db.password
    _ = app.api_key

    # Now make the backend flake — but not permanently, so retry can recover.
    inner.transient_fail_count = 10

    errors: list[Exception] = []
    barrier = threading.Barrier(50)

    def worker() -> None:
        barrier.wait()
        try:
            for _ in range(10):
                assert app.db.password == "pw1"
                assert app.api_key == "sk1"
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_rotation_with_retry_layer_visible_after_refresh() -> None:
    inner, app = _build_app()
    assert app.db.password == "pw1"

    inner.data["/prod/db/password"] = "pw2"
    # Cached value still served.
    assert app.db.password == "pw1"
    assert app.db.refresh("password") == "pw2"
    assert app.db.password == "pw2"


def test_strict_mode_raises_when_no_stale_value_available() -> None:
    """Without stale_on_error, the same outage surfaces as TransientError."""

    class StrictApp(SecretModel):  # default: stale_on_error=False
        api: str = Secret("/api", ttl=0.05)

    inner = FaultyBackend({"/api": "sk"})
    inner.transient_fail_count = 100
    backend = RetryingBackend(
        inner,
        max_attempts=2,
        base_delay=0.001,
        max_delay=0.01,
        sleep=lambda _d: None,
        jitter=False,
    )
    app = StrictApp(backend=backend)
    with pytest.raises(TransientError):
        _ = app.api


# --------------------------------------------------------------------------- prefetch + concurrent reads


def test_prefetch_then_concurrent_hot_reads_no_backend_calls() -> None:
    """Use a non-expiring TTL here — the module-level DbConfig has a 50ms
    TTL for the stale-on-error tests above, which would expire mid-read."""
    backend = MockBackend(
        {"/prod/db/password": "pw1", "/prod/api/key": "sk1"}
    )

    class HotDb(SecretModel):
        password: str = Secret("/{stage}/db/password")  # ttl=None: cache forever

    class HotApp(SecretModel, validate="fetch"):
        stage: str
        db: HotDb
        api_key: str = Secret("/{stage}/api/key")

    app = HotApp(stage="prod", db={}, backend=backend)
    backend.reset_calls()

    # Many threads, many reads — none should reach the backend.
    barrier = threading.Barrier(20)

    def worker() -> None:
        barrier.wait()
        for _ in range(50):
            assert app.db.password == "pw1"
            assert app.api_key == "sk1"

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert backend.calls == []
