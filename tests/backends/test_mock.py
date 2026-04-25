from __future__ import annotations

import pytest

from vaultly.errors import SecretNotFoundError
from vaultly.testing.mock import MockBackend


def test_get_returns_value():
    b = MockBackend({"/x": "v"})
    assert b.get("/x") == "v"


def test_get_tracks_calls():
    b = MockBackend({"/x": "v"})
    b.get("/x")
    b.get("/x")
    assert b.calls == [("/x", None), ("/x", None)]


def test_missing_raises():
    b = MockBackend({})
    with pytest.raises(SecretNotFoundError, match="/nope"):
        b.get("/nope")


def test_missing_still_records_call():
    b = MockBackend({})
    with pytest.raises(SecretNotFoundError):
        b.get("/nope")
    assert b.calls == [("/nope", None)]


def test_batch_default_uses_get():
    b = MockBackend({"/a": "1", "/b": "2"})
    assert b.get_batch(["/a", "/b"]) == {"/a": "1", "/b": "2"}
    assert b.calls == [("/a", None), ("/b", None)]


def test_get_with_version():
    b = MockBackend(versions={("/x", 2): "v2", ("/x", 3): "v3"})
    assert b.get("/x", version=2) == "v2"
    assert b.get("/x", version=3) == "v3"
    assert b.calls == [("/x", 2), ("/x", 3)]


def test_versioned_miss():
    b = MockBackend(versions={("/x", 1): "v1"})
    with pytest.raises(SecretNotFoundError, match="@5"):
        b.get("/x", version=5)


def test_reset_calls():
    b = MockBackend({"/x": "v"})
    b.get("/x")
    b.reset_calls()
    assert b.calls == []


def test_constructs_without_data():
    b = MockBackend()
    with pytest.raises(SecretNotFoundError):
        b.get("/x")
