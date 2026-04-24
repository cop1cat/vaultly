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
    assert b.calls == ["/x", "/x"]


def test_missing_raises():
    b = MockBackend({})
    with pytest.raises(SecretNotFoundError, match="/nope"):
        b.get("/nope")


def test_missing_still_records_call():
    b = MockBackend({})
    with pytest.raises(SecretNotFoundError):
        b.get("/nope")
    assert b.calls == ["/nope"]


def test_batch_default_uses_get():
    b = MockBackend({"/a": "1", "/b": "2"})
    assert b.get_batch(["/a", "/b"]) == {"/a": "1", "/b": "2"}
    assert b.calls == ["/a", "/b"]


def test_reset_calls():
    b = MockBackend({"/x": "v"})
    b.get("/x")
    b.reset_calls()
    assert b.calls == []


def test_constructs_without_data():
    b = MockBackend()
    with pytest.raises(SecretNotFoundError):
        b.get("/x")
