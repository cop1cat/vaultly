"""Regressions for the third review pass:

#1: copy.copy and copy.deepcopy must be blocked, not just model_copy.
#2: subclass with name-collision validator can break wiring (renamed parent).
#4: Optional[T] / T | None annotations must cast through to T.
#7: cast / transform exceptions surface as VaultlyError, not the raw type.
#9: MockBackend is importable from `vaultly`.
"""

from __future__ import annotations

import copy

import pytest

import vaultly
from vaultly import (
    ConfigError,
    MockBackend,
    Secret,
    SecretModel,
    VaultlyError,
)

# --------------------------------------------------------------------------- #1


class App(SecretModel):
    n: int = Secret("/n")


def _backend() -> MockBackend:
    return MockBackend({"/n": "42"})


def test_copy_copy_raises():
    c = App(backend=_backend())
    with pytest.raises(NotImplementedError, match="copy"):
        copy.copy(c)


def test_copy_deepcopy_raises():
    c = App(backend=_backend())
    with pytest.raises(NotImplementedError, match="copy"):
        copy.deepcopy(c)


def test_model_copy_still_raises():
    c = App(backend=_backend())
    with pytest.raises(NotImplementedError):
        c.model_copy()


# --------------------------------------------------------------------------- #2


def test_renamed_validator_does_not_collide_with_user_finalize():
    """A user defining `_vaultly_finalize` (the OLD name) must not break us."""
    from typing import Self as _Self

    from pydantic import model_validator

    user_ran: list[bool] = []

    class UserOverrides(SecretModel):
        stage: str
        n: int = Secret("/{stage}/n")

        # The user picks the same name our v0.0 internal validator used.
        # Our new name is `_vaultly_finalize_internal`, so theirs runs in
        # parallel and ours still does the wiring.
        @model_validator(mode="after")
        def _vaultly_finalize(self) -> _Self:
            user_ran.append(True)
            return self

    c = UserOverrides(stage="prod", backend=MockBackend({"/prod/n": "42"}))
    assert user_ran == [True]
    assert c.n == 42  # path validation + fetch still work


# --------------------------------------------------------------------------- #4


def test_optional_int_casts_correctly():
    class Opt(SecretModel):
        port: int | None = Secret("/port")

    o = Opt(backend=MockBackend({"/port": "8080"}))
    assert o.port == 8080
    assert isinstance(o.port, int)


def test_optional_dict_casts_correctly():
    class Opt(SecretModel):
        flags: dict | None = Secret("/flags")

    o = Opt(backend=MockBackend({"/flags": '{"a": 1}'}))
    assert o.flags == {"a": 1}


def test_optional_bool_casts_correctly():
    class Opt(SecretModel):
        debug: bool | None = Secret("/debug")

    o = Opt(backend=MockBackend({"/debug": "true"}))
    assert o.debug is True


def test_three_arm_union_falls_back_to_raw():
    """`int | str | None` is ambiguous — we don't pick a cast, return raw."""

    class Weird(SecretModel):
        x: int | str | None = Secret("/x")

    w = Weird(backend=MockBackend({"/x": "42"}))
    # Falls back to raw string — documented behavior.
    assert w.x == "42"


# --------------------------------------------------------------------------- #7


def test_cast_value_error_wraps_to_config_error():
    class Bad(SecretModel):
        n: int = Secret("/n")

    c = Bad(backend=MockBackend({"/n": "not-an-int"}))
    with pytest.raises(ConfigError, match="failed to cast") as ei:
        _ = c.n
    # Underlying ValueError is preserved as `__cause__`.
    assert isinstance(ei.value.__cause__, ValueError)
    # And it's catchable as VaultlyError.
    assert isinstance(ei.value, VaultlyError)


def test_transform_exception_wraps_to_config_error():
    def boom(_s: str) -> str:
        msg = "transform exploded"
        raise RuntimeError(msg)

    class T(SecretModel):
        x: str = Secret("/x", transform=boom)

    c = T(backend=MockBackend({"/x": "v"}))
    with pytest.raises(ConfigError, match="transform exploded") as ei:
        _ = c.x
    assert isinstance(ei.value.__cause__, RuntimeError)


def test_bool_parse_failure_wraps():
    class B(SecretModel):
        flag: bool = Secret("/flag")

    c = B(backend=MockBackend({"/flag": "maybe"}))
    with pytest.raises(ConfigError, match="cannot parse bool"):
        _ = c.flag


def test_existing_vaultly_error_passes_through():
    """If `transform` itself raises VaultlyError, don't double-wrap."""

    def transform(_s: str) -> str:
        msg = "real config issue"
        raise ConfigError(msg)

    class T(SecretModel):
        x: str = Secret("/x", transform=transform)

    c = T(backend=MockBackend({"/x": "v"}))
    with pytest.raises(ConfigError, match="real config issue") as ei:
        _ = c.x
    # The cause is the transform's exception itself, not wrapped.
    assert ei.value.__cause__ is None


# --------------------------------------------------------------------------- #9


def test_mockbackend_is_publicly_exported():
    assert "MockBackend" in vaultly.__all__
    assert vaultly.MockBackend is MockBackend
