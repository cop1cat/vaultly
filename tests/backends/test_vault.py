from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import hvac.exceptions as hvac_exc
import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

from vaultly.backends.vault import VaultBackend
from vaultly.errors import AuthError, SecretNotFoundError, TransientError


class FakeKVv2:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self.store = store
        self.calls: list[tuple[str, str]] = []
        self.behavior: Any = None  # if set, raise this when called

    def read_secret_version(self, *, path: str, mount_point: str, **_: Any) -> dict[str, Any]:
        self.calls.append((mount_point, path))
        if self.behavior is not None:
            raise self.behavior
        if path not in self.store:
            raise hvac_exc.InvalidPath(f"no such path: {path}")
        return {"data": {"data": self.store[path], "metadata": {}}}


class FakeClient:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self.kv = FakeKVv2(store)
        self.token: str | None = None
        # Mirror hvac.Client.secrets.kv.v2 access path.
        self.secrets = SimpleNamespace(kv=SimpleNamespace(v2=self.kv))


def _backend(store: dict[str, dict[str, Any]] | None = None, **kw: Any) -> VaultBackend:
    fc = FakeClient(store or {})
    return VaultBackend(client=fc, **kw)


# --------------------------------------------------------------------------- happy path


def test_default_key_returns_value_field():
    b = _backend({"db/password": {"value": "s3cr3t"}})
    assert b.get("db/password") == "s3cr3t"


def test_explicit_key_via_colon():
    b = _backend({"db/creds": {"username": "admin", "password": "s3cr3t"}})
    assert b.get("db/creds:password") == "s3cr3t"
    assert b.get("db/creds:username") == "admin"


def test_leading_slash_stripped():
    b = _backend({"db/password": {"value": "s3cr3t"}})
    assert b.get("/db/password") == "s3cr3t"


def test_uses_configured_mount_point():
    b = _backend({"x": {"value": "v"}}, mount_point="kv")
    b.get("x")
    assert b._client.kv.calls == [("kv", "x")]


def test_default_key_is_configurable():
    b = _backend({"x": {"secret": "s"}}, default_key="secret")
    assert b.get("x") == "s"


# --------------------------------------------------------------------------- error mapping


def test_invalid_path_maps_to_secret_not_found():
    b = _backend({})
    with pytest.raises(SecretNotFoundError, match="not found"):
        b.get("missing/path")


def test_missing_key_in_data_maps_to_secret_not_found():
    b = _backend({"db/creds": {"username": "u"}})
    with pytest.raises(SecretNotFoundError, match="no key 'password'"):
        b.get("db/creds:password")


def test_forbidden_maps_to_auth_error():
    b = _backend({"x": {"value": "v"}})
    b._client.kv.behavior = hvac_exc.Forbidden("denied")
    with pytest.raises(AuthError, match="denied"):
        b.get("x")


def test_unauthorized_maps_to_auth_error():
    b = _backend({"x": {"value": "v"}})
    b._client.kv.behavior = hvac_exc.Unauthorized("bad token")
    with pytest.raises(AuthError, match="invalid or expired"):
        b.get("x")


def test_server_error_maps_to_transient():
    b = _backend({"x": {"value": "v"}})
    b._client.kv.behavior = hvac_exc.InternalServerError("oops")
    with pytest.raises(TransientError, match="server error"):
        b.get("x")


def test_connection_error_maps_to_transient():
    b = _backend({"x": {"value": "v"}})
    b._client.kv.behavior = RequestsConnectionError("connect refused")
    with pytest.raises(TransientError, match="connection error"):
        b.get("x")


def test_unknown_vault_error_maps_to_transient():
    b = _backend({"x": {"value": "v"}})
    b._client.kv.behavior = hvac_exc.VaultError("weird")
    with pytest.raises(TransientError, match="VaultError"):
        b.get("x")


# --------------------------------------------------------------------------- batch


def test_get_batch_uses_default_serial_implementation():
    b = _backend({"a": {"value": "1"}, "b": {"value": "2"}})
    assert b.get_batch(["a", "b"]) == {"a": "1", "b": "2"}
    assert len(b._client.kv.calls) == 2


# --------------------------------------------------------------------------- token renewal


def test_token_factory_renews_on_unauthorized():
    fc = FakeClient({"x": {"value": "v"}})
    fc.token = "old"

    # First call: 401. After token swap: success.
    fail_first = [True]

    def behavior_then_pass(**_kw: Any) -> dict[str, Any]:
        if fail_first[0]:
            fail_first[0] = False
            raise hvac_exc.Unauthorized("expired")
        return {"data": {"data": {"value": "v"}, "metadata": {}}}

    fc.kv.read_secret_version = behavior_then_pass

    factory_calls = [0]

    def factory() -> str:
        factory_calls[0] += 1
        return "new-token"

    b = VaultBackend(client=fc, token_factory=factory)
    assert b.get("x") == "v"
    assert factory_calls[0] == 1
    assert fc.token == "new-token"


def test_token_factory_failure_propagates_as_auth_error():
    fc = FakeClient({"x": {"value": "v"}})
    fc.kv.behavior = hvac_exc.Unauthorized("expired")

    def factory() -> str:
        msg = "no token for you"
        raise RuntimeError(msg)

    b = VaultBackend(client=fc, token_factory=factory)
    with pytest.raises(AuthError, match="token_factory raised"):
        b.get("x")


def test_repeated_unauthorized_after_renewal_raises_auth_error():
    fc = FakeClient({"x": {"value": "v"}})
    fc.kv.behavior = hvac_exc.Unauthorized("nope")

    b = VaultBackend(client=fc, token_factory=lambda: "still-bad")
    with pytest.raises(AuthError, match="still rejects renewed token"):
        b.get("x")
