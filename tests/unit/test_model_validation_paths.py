"""Tests for the construction paths that go through validators rather than
__init__ — `model_validate`, `model_validate_json`, and `model_copy` guards."""

from __future__ import annotations

import pytest

from vaultly import MissingContextVariableError, Secret, SecretModel
from vaultly.testing.mock import MockBackend


class App(SecretModel):
    stage: str = "dev"
    db_password: str = Secret("/db/{stage}/password")


def _backend() -> MockBackend:
    return MockBackend({"/db/prod/password": "s3cr3t"})


def test_model_validate_dict_runs_path_validation():
    """model_validate doesn't go through __init__; it must still validate.

    We use a nested-child typo (which is never deferred — only own-paths
    are deferred for the could-be-nested case).
    """

    class InnerTypo(SecretModel):
        api: str = Secret("/{stge}/x")  # typo: stge instead of stage

    class Root(SecretModel):
        stage: str = "dev"
        inner: InnerTypo

    with pytest.raises(MissingContextVariableError, match="stge"):
        Root.model_validate({"stage": "prod", "inner": {}, "backend": _backend()})


def test_model_validate_dict_constructs_normally():
    c = App.model_validate({"stage": "prod", "backend": _backend()})
    assert c.db_password == "s3cr3t"


def test_model_validate_json_works():
    """JSON path doesn't include backend (not serializable); inject post-construct
    by validating with backend in the dict via context-passing pattern."""
    # Backends aren't JSON; this test just confirms model_validate_json
    # constructs the scalar shape correctly; actual fetching needs a backend.
    c = App.model_validate_json('{"stage": "prod"}')
    assert c.stage == "prod"
    # No backend → fetching fails cleanly (we already test that elsewhere).


def test_model_dump_json_masks_secrets():
    c = App(stage="prod", backend=_backend())
    _ = c.db_password
    j = c.model_dump_json()
    assert "s3cr3t" not in j
    assert '"db_password":"***"' in j


def test_model_validate_with_fetch_mode_prefetches():
    class Eager(SecretModel, validate="fetch"):
        stage: str = "dev"
        db_password: str = Secret("/db/{stage}/password")

    b = MockBackend({"/db/prod/password": "s3cr3t"})
    c = Eager.model_validate({"stage": "prod", "backend": b})
    # prefetched at construction
    assert b.calls == [("/db/prod/password", None)]
    _ = c.db_password
    # still only the one call — served from cache
    assert b.calls == [("/db/prod/password", None)]


def test_model_copy_is_disabled():
    c = App(stage="prod", backend=_backend())
    with pytest.raises(NotImplementedError, match="model_copy"):
        c.model_copy()


def test_model_copy_deep_also_disabled():
    c = App(stage="prod", backend=_backend())
    with pytest.raises(NotImplementedError, match="model_copy"):
        c.model_copy(deep=True)
