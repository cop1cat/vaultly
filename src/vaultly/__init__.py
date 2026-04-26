"""vaultly — declarative Pydantic-native secrets manager.

Optional backends (`AWSSSMBackend`, `VaultBackend`) live in their own
submodules and import their SDKs lazily; they are not re-exported here so
that `import vaultly` works without boto3 / hvac installed. Use:

    from vaultly.backends.aws_ssm import AWSSSMBackend
    from vaultly.backends.vault import VaultBackend
"""

from __future__ import annotations

import logging as _logging

# Standard library practice: ship a NullHandler so emit-without-config
# does not flood stderr via the lastResort handler. Apps that *want* to
# see vaultly logs can attach their own handler / set propagate=True.
_logging.getLogger("vaultly").addHandler(_logging.NullHandler())

from vaultly.backends.base import Backend  # noqa: E402
from vaultly.backends.env import EnvBackend  # noqa: E402
from vaultly.backends.retrying import RetryingBackend  # noqa: E402
from vaultly.core.model import SecretModel  # noqa: E402
from vaultly.core.secret import Secret  # noqa: E402
from vaultly.errors import (  # noqa: E402
    AuthError,
    ConfigError,
    MissingContextVariableError,
    SecretNotFoundError,
    TransientError,
    VaultlyError,
)
from vaultly.testing.mock import MockBackend  # noqa: E402

__all__ = [
    "AuthError",
    "Backend",
    "ConfigError",
    "EnvBackend",
    "MissingContextVariableError",
    "MockBackend",
    "RetryingBackend",
    "Secret",
    "SecretModel",
    "SecretNotFoundError",
    "TransientError",
    "VaultlyError",
]
