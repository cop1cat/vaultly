from __future__ import annotations

import pytest

from vaultly import (
    ConfigError,
    MissingContextVariableError,
    Secret,
    SecretModel,
)
from vaultly.testing.mock import MockBackend

# --------------------------------------------------------------------------- basics


class AppConfig(SecretModel):
    stage: str = "dev"
    debug: bool = False
    db_password: str = Secret("/db/{stage}/password", ttl=60)
    api_key: str = Secret("/services/openai/key")
    max_conns: int = Secret("/db/{stage}/max_conns")


def _backend() -> MockBackend:
    return MockBackend(
        {
            "/db/prod/password": "s3cr3t",
            "/services/openai/key": "sk-abc",
            "/db/prod/max_conns": "42",
        }
    )


def test_lazy_fetch_returns_casted_value():
    b = _backend()
    c = AppConfig(stage="prod", backend=b)
    assert c.db_password == "s3cr3t"
    assert c.max_conns == 42
    assert isinstance(c.max_conns, int)


def test_cache_avoids_refetch():
    b = _backend()
    c = AppConfig(stage="prod", backend=b)
    _ = c.db_password
    _ = c.db_password
    _ = c.db_password
    assert b.calls == ["/db/prod/password"]


def test_masking_in_repr():
    c = AppConfig(stage="prod", backend=_backend())
    _ = c.db_password  # force fetch, should still be masked
    r = repr(c)
    assert "s3cr3t" not in r
    assert "***" in r


def test_masking_in_model_dump():
    c = AppConfig(stage="prod", backend=_backend())
    _ = c.db_password
    data = c.model_dump()
    assert data["db_password"] == "***"
    assert data["api_key"] == "***"
    assert data["stage"] == "prod"


def test_regular_fields_still_work():
    c = AppConfig(stage="prod", debug=True, backend=_backend())
    assert c.stage == "prod"
    assert c.debug is True


# --------------------------------------------------------------------------- path validation


def test_standalone_broken_defers_to_fetch():
    """A lone model whose own secrets can't resolve defers until fetch.

    Rationale: we can't tell at init whether the user will wire this into a
    parent later. Catching the error on fetch gives a clean failure mode
    either way.
    """

    class Broken(SecretModel):
        db_password: str = Secret("/db/{stage}/password")

    c = Broken(backend=_backend())  # deferred — OK
    with pytest.raises(MissingContextVariableError, match="stage"):
        _ = c.db_password


def test_paths_with_no_vars_are_fine():
    class Simple(SecretModel):
        api_key: str = Secret("/services/openai/key")

    c = Simple(backend=_backend())
    assert c.api_key == "sk-abc"


def test_validate_none_skips_check():
    class Broken(SecretModel):
        _vaultly_validate = "none"
        db_password: str = Secret("/db/{stage}/password")

    # no `stage` field, but validate="none" — construction succeeds
    c = Broken(backend=_backend())
    # fetch still fails later when path can't be resolved
    with pytest.raises(MissingContextVariableError, match="stage"):
        _ = c.db_password


# --------------------------------------------------------------------------- no backend


def test_fetch_without_backend_raises():
    c = AppConfig(stage="prod")  # backend=None
    with pytest.raises(ConfigError, match="backend"):
        _ = c.db_password


# --------------------------------------------------------------------------- refresh


def test_refresh_invalidates_and_refetches():
    b = _backend()
    c = AppConfig(stage="prod", backend=b)
    assert c.db_password == "s3cr3t"
    b.data["/db/prod/password"] = "new-pw"
    # cached value still returned
    assert c.db_password == "s3cr3t"
    # refresh forces refetch
    assert c.refresh("db_password") == "new-pw"
    assert c.db_password == "new-pw"


def test_refresh_all_clears_cache():
    b = _backend()
    c = AppConfig(stage="prod", backend=b)
    _ = c.db_password
    _ = c.api_key
    b.reset_calls()
    c.refresh_all()
    _ = c.db_password
    _ = c.api_key
    assert sorted(b.calls) == sorted(["/db/prod/password", "/services/openai/key"])


def test_refresh_unknown_field_raises():
    c = AppConfig(stage="prod", backend=_backend())
    with pytest.raises(ValueError, match="is not a secret field"):
        c.refresh("stage")


# --------------------------------------------------------------------------- prefetch / validate="fetch"


def test_prefetch_populates_cache_via_batch():
    b = _backend()
    c = AppConfig(stage="prod", backend=b)
    b.reset_calls()
    c.prefetch()
    # every secret path was fetched once; subsequent access uses cache
    assert sorted(b.calls) == sorted(
        ["/db/prod/password", "/services/openai/key", "/db/prod/max_conns"]
    )
    b.reset_calls()
    assert c.db_password == "s3cr3t"
    assert c.api_key == "sk-abc"
    assert c.max_conns == 42
    assert b.calls == []


def test_validate_fetch_prefetches_at_init():
    class Eager(SecretModel):
        _vaultly_validate = "fetch"
        stage: str = "dev"
        db_password: str = Secret("/db/{stage}/password")

    b = MockBackend({"/db/prod/password": "s3cr3t"})
    c = Eager(stage="prod", backend=b)
    assert b.calls == ["/db/prod/password"]
    # access is cache-only
    assert c.db_password == "s3cr3t"
    assert b.calls == ["/db/prod/password"]


# --------------------------------------------------------------------------- nested models


class DbConfig(SecretModel):
    password: str = Secret("/db/{stage}/password")
    max_conns: int = Secret("/db/{stage}/max_conns")


class NestedApp(SecretModel):
    stage: str
    db: DbConfig
    api_key: str = Secret("/services/{stage}/key")


def test_nested_inherits_backend_and_context():
    b = MockBackend(
        {
            "/db/prod/password": "s3cr3t",
            "/db/prod/max_conns": "42",
            "/services/prod/key": "sk",
        }
    )
    c = NestedApp(stage="prod", db=DbConfig(), backend=b)
    assert c.api_key == "sk"
    assert c.db.password == "s3cr3t"
    assert c.db.max_conns == 42


def test_nested_shares_root_cache():
    b = MockBackend(
        {"/db/prod/password": "v", "/db/prod/max_conns": "1", "/services/prod/key": "k"}
    )
    c = NestedApp(stage="prod", db=DbConfig(), backend=b)
    _ = c.db.password
    _ = c.db.password
    assert b.calls == ["/db/prod/password"]


def test_nested_missing_context_var_detected_at_root():
    class BadNested(SecretModel):
        password: str = Secret("/db/{stage}/password")

    class BadRoot(SecretModel):
        # no `stage` field
        db: BadNested

    with pytest.raises(MissingContextVariableError, match="stage"):
        BadRoot(db=BadNested(), backend=MockBackend({}))


def test_standalone_nested_without_context_fails_on_fetch():
    """Standalone construction defers validation; first fetch surfaces the error."""

    class NeedsStage(SecretModel):
        password: str = Secret("/db/{stage}/password")

    c = NeedsStage(backend=MockBackend({}))  # deferred — OK
    with pytest.raises(MissingContextVariableError, match="stage"):
        _ = c.password


def test_standalone_nested_as_root_works():
    """A SecretModel used directly (not nested) is its own root."""
    b = MockBackend({"/services/openai/key": "sk"})

    class Standalone(SecretModel):
        api_key: str = Secret("/services/openai/key")

    c = Standalone(backend=b)
    assert c.api_key == "sk"
