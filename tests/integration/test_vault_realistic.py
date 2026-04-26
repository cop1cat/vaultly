"""Realistic Vault scenarios.

We don't spin up a real Vault container in CI here (would need docker on the
runner) — instead, the FakeClient mimics KV v2's actual response shape and
exception types, which `tests/backends/test_vault.py` already validates per
operation. These tests stress full lifecycles:

- App config tree built from Vault, with `path:key` syntax for fields-in-dict
- Token renewal during a fetch storm
- Mixed JSON value types in a single secret (str + dict + int)
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import hvac.exceptions as hvac_exc

from vaultly import RetryingBackend, Secret, SecretModel
from vaultly.backends.vault import VaultBackend


class _RealisticKV:
    """Mimics hvac KV v2 response shape; supports auth-fail injection."""

    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self.store = store
        self.token_check: bool = False
        self.current_valid_token: str | None = None
        self.calls: list[tuple[str, Any]] = []

    def read_secret_version(
        self,
        *,
        path: str,
        mount_point: str,
        version: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append((path, version))
        if self.token_check and self._client_token != self.current_valid_token:
            raise hvac_exc.Unauthorized("token rejected")
        if path not in self.store:
            raise hvac_exc.InvalidPath(f"missing: {path}")
        data = self.store[path]
        return {"data": {"data": data, "metadata": {"version": version or 1}}}

    # set later by the FakeClient wrapper
    _client_token: str | None = None


class _RealisticClient:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self.kv = _RealisticKV(store)
        self.secrets = SimpleNamespace(kv=SimpleNamespace(v2=self.kv))
        self._token: str | None = None

    @property
    def token(self) -> str | None:
        return self._token

    @token.setter
    def token(self, value: str | None) -> None:
        self._token = value
        self.kv._client_token = value


# --------------------------------------------------------------------------- multi-field secret entry


def test_path_key_syntax_pulls_distinct_fields_from_one_entry() -> None:
    """A single Vault KV entry holding many fields is read as separate
    secrets via the `:key` suffix."""
    client = _RealisticClient(
        {
            "prod/db/creds": {
                "username": "appuser",
                "password": "s3cr3t",
                "host": "db.prod.local",
                "port": 5432,
            }
        }
    )
    backend = VaultBackend(client=client)

    class App(SecretModel):
        username: str = Secret("/prod/db/creds:username")
        password: str = Secret("/prod/db/creds:password")
        host: str = Secret("/prod/db/creds:host")
        port: int = Secret("/prod/db/creds:port")

    app = App(backend=backend)

    assert app.username == "appuser"
    assert app.password == "s3cr3t"
    assert app.host == "db.prod.local"
    assert app.port == 5432
    assert isinstance(app.port, int)


def test_one_vault_read_per_secret_path_independent_of_keys() -> None:
    """Reading 4 fields from the same entry results in 4 reads (we don't
    yet coalesce by path — documented). Cache de-dups subsequent reads."""
    client = _RealisticClient(
        {"prod/creds": {"a": "1", "b": "2", "c": "3", "d": "4"}}
    )
    backend = VaultBackend(client=client)

    class App(SecretModel):
        a: str = Secret("/prod/creds:a")
        b: str = Secret("/prod/creds:b")
        c: str = Secret("/prod/creds:c")
        d: str = Secret("/prod/creds:d")

    app = App(backend=backend)
    _ = app.a, app.b, app.c, app.d
    # 4 reads, one per field (each has a unique cache key because
    # `resolved_path` includes `:key`).
    assert len(client.kv.calls) == 4

    # Subsequent reads come from cache.
    n = len(client.kv.calls)
    _ = app.a, app.b
    assert len(client.kv.calls) == n


# --------------------------------------------------------------------------- token renewal


def test_token_factory_recovers_under_high_concurrency() -> None:
    """Many threads hit the backend right after token expiry; the factory
    runs once per concurrent unique fetch, then everyone proceeds."""
    client = _RealisticClient({"x": {"value": "v"}})
    client.kv.token_check = True
    client.kv.current_valid_token = "good"
    client.token = "expired"  # so first call fails

    factory_calls = [0]
    factory_lock = threading.Lock()

    def factory() -> str:
        with factory_lock:
            factory_calls[0] += 1
        return "good"

    backend = VaultBackend(client=client, token_factory=factory)

    class App(SecretModel):
        x: str = Secret("/x")

    app = App(backend=backend)

    barrier = threading.Barrier(30)

    def worker() -> None:
        barrier.wait()
        assert app.x == "v"

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # At least one factory call (the per-key fetch lock serializes them
    # for the same key — first thread refreshes, others read the cache).
    assert factory_calls[0] >= 1
    assert client.token == "good"


def test_token_factory_is_invoked_only_once_under_per_key_lock() -> None:
    """Single key, many readers, expired token — exactly one renewal."""
    client = _RealisticClient({"x": {"value": "v"}})
    client.kv.token_check = True
    client.kv.current_valid_token = "good"
    client.token = "bad"

    factory_calls = [0]

    def factory() -> str:
        factory_calls[0] += 1
        return "good"

    backend = VaultBackend(client=client, token_factory=factory)

    class App(SecretModel):
        x: str = Secret("/x")

    app = App(backend=backend)

    barrier = threading.Barrier(20)

    def worker() -> None:
        barrier.wait()
        assert app.x == "v"

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Per-key locking + cache double-check means exactly one underlying
    # fetch sequence runs to completion → exactly one factory invocation.
    assert factory_calls[0] == 1


# --------------------------------------------------------------------------- mixed value types


def test_kv_entry_with_mixed_value_types() -> None:
    """A real Vault KV entry can mix strings, ints, dicts. Each field
    addresses one key and gets the right typed cast."""
    client = _RealisticClient(
        {
            "prod/svc": {
                "name": "billing-api",
                "replicas": 3,
                "limits": {"cpu": "500m", "memory": "1Gi"},
                "enabled": True,
            }
        }
    )
    backend = VaultBackend(client=client)

    class Svc(SecretModel):
        name: str = Secret("/prod/svc:name")
        replicas: int = Secret("/prod/svc:replicas")
        limits: dict = Secret("/prod/svc:limits")
        enabled: bool = Secret("/prod/svc:enabled")

    s = Svc(backend=backend)
    assert s.name == "billing-api"
    assert s.replicas == 3
    assert s.limits == {"cpu": "500m", "memory": "1Gi"}
    assert s.enabled is True


# --------------------------------------------------------------------------- versioned reads


def test_versioned_read_passes_version_to_hvac() -> None:
    """vaultly's `Secret(version=N)` translates to hvac's version kwarg."""
    client = _RealisticClient({"prod/secret": {"value": "v"}})
    backend = VaultBackend(client=client)

    class App(SecretModel):
        pinned: str = Secret("/prod/secret", version=2)
        latest: str = Secret("/prod/secret")

    app = App(backend=backend)
    _ = app.pinned, app.latest

    versioned_calls = [v for _, v in client.kv.calls]
    assert 2 in versioned_calls  # pinned arrived as version=2
    assert None in versioned_calls  # latest arrived without version


# --------------------------------------------------------------------------- retry over Vault


def test_retrying_backend_over_vault() -> None:
    """RetryingBackend layered over Vault recovers from transient 500s."""
    client = _RealisticClient({"x": {"value": "v"}})
    inner = VaultBackend(client=client)

    fail_left = [2]
    real_read = client.kv.read_secret_version

    def flaky_read(**kw: Any) -> dict[str, Any]:
        if fail_left[0] > 0:
            fail_left[0] -= 1
            raise hvac_exc.InternalServerError("server flaky")
        return real_read(**kw)

    object.__setattr__(client.kv, "read_secret_version", flaky_read)

    backend = RetryingBackend(
        inner,
        max_attempts=5,
        base_delay=0.001,
        max_delay=0.01,
        sleep=lambda _d: None,
        jitter=False,
    )

    class App(SecretModel):
        x: str = Secret("/x")

    app = App(backend=backend)
    assert app.x == "v"
    assert fail_left[0] == 0  # all flakes consumed
