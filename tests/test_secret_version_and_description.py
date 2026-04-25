"""Tests for `version=` and `description=` on Secret(...)."""

from __future__ import annotations

import pytest

from vaultly import ConfigError, Secret, SecretModel
from vaultly.core.secret import _SecretSpec
from vaultly.testing.mock import MockBackend

# --------------------------------------------------------------------------- spec record


def test_spec_records_version_and_description():
    info = Secret("/db/password", version=3, description="primary db creds")
    [spec] = [m for m in info.metadata if isinstance(m, _SecretSpec)]
    assert spec.version == 3
    assert spec.description == "primary db creds"


def test_spec_repr_includes_extras():
    info = Secret("/x", version=2, description="d")
    [spec] = [m for m in info.metadata if isinstance(m, _SecretSpec)]
    r = repr(spec)
    assert "version=2" in r
    assert "description='d'" in r


def test_spec_defaults_are_none():
    info = Secret("/x")
    [spec] = [m for m in info.metadata if isinstance(m, _SecretSpec)]
    assert spec.version is None
    assert spec.description is None


# --------------------------------------------------------------------------- version: fetch


class Versioned(SecretModel):
    pinned: str = Secret("/db/password", version=2)
    latest: str = Secret("/db/password")


def test_version_passed_to_backend():
    b = MockBackend(
        data={"/db/password": "current"},
        versions={("/db/password", 2): "older"},
    )
    c = Versioned(backend=b)
    assert c.pinned == "older"
    assert c.latest == "current"
    # Two distinct backend calls — one per version (incl. None).
    assert set(b.calls) == {("/db/password", 2), ("/db/password", None)}


def test_versioned_and_unversioned_cache_separately():
    b = MockBackend(
        data={"/db/password": "current"},
        versions={("/db/password", 2): "older"},
    )
    c = Versioned(backend=b)
    _ = c.pinned
    _ = c.latest
    b.reset_calls()
    # Both already cached under distinct keys; no further backend calls.
    _ = c.pinned
    _ = c.latest
    assert b.calls == []


def test_refresh_invalidates_only_specified_version():
    b = MockBackend(
        data={"/db/password": "current"},
        versions={("/db/password", 2): "older"},
    )
    c = Versioned(backend=b)
    _ = c.pinned
    _ = c.latest
    b.reset_calls()
    c.refresh("pinned")
    # Only the pinned version was refetched.
    assert b.calls == [("/db/password", 2)]


# --------------------------------------------------------------------------- version: prefetch


def test_prefetch_uses_serial_for_versioned_and_batch_for_rest():
    class Mixed(SecretModel):
        pinned: str = Secret("/p", version=1)
        a: str = Secret("/a")
        b: str = Secret("/b")

    backend_data = {"/a": "1", "/b": "2"}
    versions: dict[tuple[str, int | str], str] = {("/p", 1): "pv"}

    class TrackingBackend(MockBackend):
        def __init__(self) -> None:
            super().__init__(data=backend_data, versions=versions)
            self.batch_calls: list[list[str]] = []

        def get_batch(self, paths):
            self.batch_calls.append(list(paths))
            return super().get_batch(paths)

    b = TrackingBackend()
    c = Mixed(backend=b)
    c.prefetch()

    # Unversioned secrets travelled via get_batch.
    assert sorted(b.batch_calls[0]) == ["/a", "/b"]
    # Versioned secret used a serial get with version.
    versioned_calls = [(p, v) for p, v in b.calls if v is not None]
    assert versioned_calls == [("/p", 1)]


# --------------------------------------------------------------------------- description in error messages


def test_description_appears_in_missing_context_error():
    class App(SecretModel):
        _vaultly_validate = "none"
        db: str = Secret(
            "/db/{stage}/password", description="postgres prod credentials"
        )

    c = App(backend=MockBackend({}))
    with pytest.raises(Exception, match="postgres prod credentials") as ei:
        _ = c.db
    # Specifically a MissingContextVariableError, but the test only cares
    # that the description string appears somewhere in the message.
    assert "stage" in str(ei.value)


def test_description_appears_in_no_backend_error():
    class App(SecretModel):
        api: str = Secret("/x", description="OpenAI key")

    c = App()  # backend=None
    with pytest.raises(ConfigError, match="OpenAI key"):
        _ = c.api
