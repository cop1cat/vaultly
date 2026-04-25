from __future__ import annotations

import threading
import time

import pytest

from vaultly.core.cache import TTLCache


def test_miss_raises_keyerror():
    c = TTLCache()
    with pytest.raises(KeyError):
        c.get("/x")


def test_set_get_roundtrip():
    c = TTLCache()
    c.set("/x", "v", ttl=None)
    assert c.get("/x") == "v"


def test_ttl_none_never_expires(monkeypatch):
    c = TTLCache()
    c.set("/x", "v", ttl=None)
    monkeypatch.setattr(time, "monotonic", lambda: time.monotonic() + 1_000_000)
    assert c.get("/x") == "v"


def test_ttl_zero_immediately_expired():
    c = TTLCache()
    c.set("/x", "v", ttl=0)
    with pytest.raises(KeyError):
        c.get("/x")


def test_ttl_positive_expires_after_window():
    c = TTLCache()
    # Use a tiny ttl and sleep briefly. Not ideal for determinism but keeps
    # the test honest about wall-clock behavior.
    c.set("/x", "v", ttl=0.01)
    time.sleep(0.05)
    with pytest.raises(KeyError):
        c.get("/x")


def test_expired_entry_is_kept_for_peek():
    """get() raises on expiry but keeps the entry around for peek_expired."""
    c = TTLCache()
    c.set("/x", "v", ttl=0)
    with pytest.raises(KeyError):
        c.get("/x")
    # Still there for stale-on-error fallback.
    assert c.peek_expired("/x") == "v"


def test_invalidate_truly_removes_expired_entry():
    c = TTLCache()
    c.set("/x", "v", ttl=0)
    c.invalidate("/x")
    with pytest.raises(KeyError):
        c.peek_expired("/x")


def test_peek_expired_returns_stale_value():
    c = TTLCache()
    c.set("/x", "stale", ttl=0.001)
    time.sleep(0.01)
    assert c.peek_expired("/x") == "stale"


def test_peek_expired_miss():
    c = TTLCache()
    with pytest.raises(KeyError):
        c.peek_expired("/x")


def test_invalidate():
    c = TTLCache()
    c.set("/x", "v", ttl=None)
    c.invalidate("/x")
    with pytest.raises(KeyError):
        c.get("/x")


def test_invalidate_missing_is_noop():
    c = TTLCache()
    c.invalidate("/missing")  # no exception


def test_clear():
    c = TTLCache()
    c.set("/a", 1, ttl=None)
    c.set("/b", 2, ttl=None)
    c.clear()
    with pytest.raises(KeyError):
        c.get("/a")
    with pytest.raises(KeyError):
        c.get("/b")


def test_concurrent_set_get():
    c = TTLCache()
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            for _ in range(200):
                c.set(f"/{i}", i, ttl=None)
                assert c.get(f"/{i}") == i
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
