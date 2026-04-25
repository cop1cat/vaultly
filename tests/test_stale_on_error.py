from __future__ import annotations

import logging
import time

import pytest

from vaultly import Secret, SecretModel
from vaultly.backends.base import Backend
from vaultly.errors import TransientError


class FlipBackend(Backend):
    """Succeeds until `fail` is set to True; then raises TransientError."""

    def __init__(self, data: dict[str, str]) -> None:
        self.data = data
        self.fail = False
        self.calls = 0

    def get(self, path: str) -> str:
        self.calls += 1
        if self.fail:
            raise TransientError("outage")
        return self.data[path]


class StaleApp(SecretModel):
    _vaultly_stale_on_error = True
    db_password: str = Secret("/db/password", ttl=0.01)


class StrictApp(SecretModel):
    # default: _vaultly_stale_on_error = False
    db_password: str = Secret("/db/password", ttl=0.01)


def test_stale_on_error_returns_cached_value_when_backend_fails():
    b = FlipBackend({"/db/password": "s3cr3t"})
    c = StaleApp(backend=b)

    assert c.db_password == "s3cr3t"

    # Wait for TTL to expire, then make the backend fail.
    time.sleep(0.02)
    b.fail = True

    # Stale-on-error: the expired cached value is returned with a warning.
    assert c.db_password == "s3cr3t"


def test_stale_on_error_logs_warning(caplog):
    b = FlipBackend({"/db/password": "s3cr3t"})
    c = StaleApp(backend=b)
    assert c.db_password == "s3cr3t"
    time.sleep(0.02)
    b.fail = True

    with caplog.at_level(logging.WARNING, logger="vaultly"):
        _ = c.db_password
    assert any("stale" in r.message for r in caplog.records)


def test_strict_mode_raises_transient_error():
    b = FlipBackend({"/db/password": "s3cr3t"})
    c = StrictApp(backend=b)

    assert c.db_password == "s3cr3t"
    time.sleep(0.02)
    b.fail = True

    with pytest.raises(TransientError):
        _ = c.db_password


def test_stale_on_error_without_cache_reraises():
    """If nothing was ever cached, TransientError must propagate even in stale mode."""
    b = FlipBackend({"/db/password": "s3cr3t"})
    b.fail = True
    c = StaleApp(backend=b)

    with pytest.raises(TransientError):
        _ = c.db_password
