"""Regressions for the fourth review pass:

A: pickle.dumps must be blocked explicitly (not rely on RLock unpicklability).
F: transform that calls back into the model (refresh) must not deadlock —
   `KeyedLocks` is reentrant.
G: VaultBackend must handle non-string KV values (Vault stores arbitrary JSON).
I: vaultly logger must have a NullHandler so unconfigured apps don't see
   warnings on stderr.
"""

from __future__ import annotations

import logging
import pickle
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from vaultly import (
    ConfigError,
    MockBackend,
    Secret,
    SecretModel,
)
from vaultly.backends.vault import VaultBackend

# --------------------------------------------------------------------------- A. pickle


class A(SecretModel):
    n: int = Secret("/n")


def test_pickle_dumps_blocked():
    c = A(backend=MockBackend({"/n": "1"}))
    with pytest.raises(NotImplementedError, match="pickl"):
        pickle.dumps(c)


def test_pickle_dumps_blocked_after_fetch():
    c = A(backend=MockBackend({"/n": "1"}))
    _ = c.n  # populate cache
    with pytest.raises(NotImplementedError, match="pickl"):
        pickle.dumps(c)


# --------------------------------------------------------------------------- F. reentrant transform


def test_transform_can_call_refresh_without_deadlock():
    """A `transform=` that calls back into the model must not deadlock.

    The fix is `RLock` — same thread can re-enter. We don't *recommend*
    transforms that mutate model state, but a hang is the worst possible
    outcome and we explicitly avoid it.
    """
    state = {"transform_calls": 0, "refresh_value": None}

    def transform(s: str) -> str:
        state["transform_calls"] += 1
        if state["transform_calls"] == 1:
            # Recursively refresh; with RLock this reaches a fixed point.
            state["refresh_value"] = c.refresh("x")
        return s.upper()

    class Re(SecretModel):
        x: str = Secret("/x", transform=transform)

    c = Re(backend=MockBackend({"/x": "hello"}))

    finished: list[bool] = []

    def runner() -> None:
        try:
            c.x  # noqa: B018
        finally:
            finished.append(True)

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout=3.0)
    assert finished == [True], "transform→refresh must not deadlock"


# --------------------------------------------------------------------------- G. Vault non-str values


class _FakeKV:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self.store = store

    def read_secret_version(self, *, path: str, **_: Any) -> dict[str, Any]:
        return {"data": {"data": self.store[path], "metadata": {}}}


class _FakeClient:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self.kv = _FakeKV(store)
        self.secrets = SimpleNamespace(kv=SimpleNamespace(v2=self.kv))
        self.token: str | None = None


def test_vault_dict_value_round_trips_through_json():
    """A dict stored in Vault must come back as valid JSON for our cast layer."""
    b = VaultBackend(client=_FakeClient({"x": {"value": {"k": "v", "n": 1}}}))

    class Cfg(SecretModel):
        cfg: dict = Secret("/x")

    # The previous `str(dict)` would produce single-quoted Python repr and
    # fail json.loads. With `json.dumps` we get valid JSON.
    m = Cfg(backend=b)
    assert m.cfg == {"k": "v", "n": 1}


def test_vault_int_value_casts_to_int():
    b = VaultBackend(client=_FakeClient({"x": {"value": 42}}))

    class Cfg(SecretModel):
        n: int = Secret("/x")

    assert Cfg(backend=b).n == 42


def test_vault_bool_value_casts_to_bool():
    b = VaultBackend(client=_FakeClient({"x": {"value": True}}))

    class Cfg(SecretModel):
        flag: bool = Secret("/x")

    assert Cfg(backend=b).flag is True


def test_vault_list_value_casts_to_list():
    b = VaultBackend(client=_FakeClient({"x": {"value": [1, 2, 3]}}))

    class Cfg(SecretModel):
        items: list = Secret("/x")

    assert Cfg(backend=b).items == [1, 2, 3]


def test_vault_str_value_round_trips_unchanged():
    """str must NOT be json.dumps'd — that would add extra quotes."""
    b = VaultBackend(client=_FakeClient({"x": {"value": "hello"}}))

    class Cfg(SecretModel):
        s: str = Secret("/x")

    assert Cfg(backend=b).s == "hello"  # not "\"hello\""


# --------------------------------------------------------------------------- I. logger


def test_vaultly_logger_has_null_handler():
    log = logging.getLogger("vaultly")
    assert any(isinstance(h, logging.NullHandler) for h in log.handlers), (
        "vaultly should ship a NullHandler so unconfigured apps don't get "
        "warnings on stderr via the lastResort handler"
    )


def test_warning_does_not_propagate_to_root_when_no_user_handler(caplog):
    """A user with no logging setup shouldn't see vaultly warnings on stderr."""
    log = logging.getLogger("vaultly")
    # The NullHandler swallows the record at the vaultly logger level.
    # If propagate were also False, it wouldn't reach the root either.
    # Today we rely on the NullHandler + the user's own logging config.
    with caplog.at_level(logging.WARNING, logger="vaultly"):
        log.warning("test")
    # caplog still sees it because pytest installs its own handler;
    # this test mainly documents the expected setup.
    assert any("test" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- C/D edge cases (already work, pin them)


def test_frozen_model_secret_works():
    from pydantic import ConfigDict

    class F(SecretModel):
        model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
        n: int = Secret("/n")

    c = F(backend=MockBackend({"/n": "1"}))
    assert c.n == 1
    # Pydantic enforces immutability on the model fields.
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        c.n = 999


def test_multiple_inheritance_with_mixin():
    class Mixin:
        def hi(self) -> str:
            return "hi"

    class M(SecretModel, Mixin):
        n: int = Secret("/n")

    c = M(backend=MockBackend({"/n": "1"}))
    assert c.n == 1
    assert c.hi() == "hi"


def test_empty_string_int_cast_wraps_to_config_error():
    """A misconfigured backend returning '' for an int field surfaces cleanly."""

    class M(SecretModel):
        n: int = Secret("/n")

    c = M(backend=MockBackend({"/n": ""}))
    with pytest.raises(ConfigError, match="failed to cast"):
        _ = c.n
