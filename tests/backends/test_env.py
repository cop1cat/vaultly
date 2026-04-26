from __future__ import annotations

import pytest

from vaultly.backends.env import EnvBackend
from vaultly.errors import SecretNotFoundError


def test_basic_get(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "s3cr3t")
    assert EnvBackend().get("/db/password") == "s3cr3t"


def test_nested_path(monkeypatch):
    monkeypatch.setenv("DB_PROD_PASSWORD", "x")
    assert EnvBackend().get("/db/prod/password") == "x"


def test_no_leading_slash(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    assert EnvBackend().get("api/key") == "k"


def test_prefix_with_trailing_underscore(monkeypatch):
    monkeypatch.setenv("MYAPP_DB_PASSWORD", "p")
    assert EnvBackend(prefix="MYAPP_").get("/db/password") == "p"


def test_prefix_auto_inserts_underscore(monkeypatch):
    monkeypatch.setenv("MYAPP_DB_PASSWORD", "p")
    assert EnvBackend(prefix="MYAPP").get("/db/password") == "p"


def test_missing_raises(monkeypatch):
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    with pytest.raises(SecretNotFoundError, match="DOES_NOT_EXIST"):
        EnvBackend().get("/does/not/exist")


def test_batch_default(monkeypatch):
    monkeypatch.setenv("A", "1")
    monkeypatch.setenv("B", "2")
    assert EnvBackend().get_batch(["/a", "/b"]) == {"/a": "1", "/b": "2"}
