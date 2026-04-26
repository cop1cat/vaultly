from __future__ import annotations

from vaultly.errors import (
    AuthError,
    ConfigError,
    MissingContextVariableError,
    SecretNotFoundError,
    TransientError,
    VaultlyError,
)


def test_all_inherit_from_base():
    for exc in (
        ConfigError,
        MissingContextVariableError,
        SecretNotFoundError,
        AuthError,
        TransientError,
    ):
        assert issubclass(exc, VaultlyError)


def test_missing_context_is_config_error():
    assert issubclass(MissingContextVariableError, ConfigError)


def test_transient_is_separate_from_secret_not_found():
    assert not issubclass(TransientError, SecretNotFoundError)
    assert not issubclass(SecretNotFoundError, TransientError)


def test_auth_is_separate_from_transient():
    assert not issubclass(AuthError, TransientError)
