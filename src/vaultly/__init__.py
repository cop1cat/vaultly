"""vaultly — declarative Pydantic-native secrets manager."""

from __future__ import annotations

from vaultly.backends.base import Backend
from vaultly.backends.env import EnvBackend
from vaultly.core.model import SecretModel
from vaultly.core.secret import Secret
from vaultly.errors import (
    AuthError,
    ConfigError,
    MissingContextVariableError,
    SecretNotFoundError,
    TransientError,
    VaultlyError,
)

__all__ = [
    "AuthError",
    "Backend",
    "ConfigError",
    "EnvBackend",
    "MissingContextVariableError",
    "Secret",
    "SecretModel",
    "SecretNotFoundError",
    "TransientError",
    "VaultlyError",
]
