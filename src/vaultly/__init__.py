"""vaultly — declarative Pydantic-native secrets manager.

Optional backends (`AWSSSMBackend`, `VaultBackend`) live in their own
submodules and import their SDKs lazily; they are not re-exported here so
that `import vaultly` works without boto3 / hvac installed. Use:

    from vaultly.backends.aws_ssm import AWSSSMBackend
    from vaultly.backends.vault import VaultBackend
"""

from __future__ import annotations

from vaultly.backends.base import Backend
from vaultly.backends.env import EnvBackend
from vaultly.backends.retrying import RetryingBackend
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
    "RetryingBackend",
    "Secret",
    "SecretModel",
    "SecretNotFoundError",
    "TransientError",
    "VaultlyError",
]
