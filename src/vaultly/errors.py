"""Exception hierarchy for vaultly.

Each backend maps its own exceptions into this hierarchy. The library itself
raises these types only; user code should catch them rather than SDK-specific
exceptions.
"""

from __future__ import annotations


class VaultlyError(Exception):
    """Base for every exception raised by vaultly."""


class ConfigError(VaultlyError):
    """Problem with the user-provided model configuration."""


class MissingContextVariableError(ConfigError):
    """A `{var}` in a secret path refers to a field that does not exist."""


class SecretNotFoundError(VaultlyError):
    """Backend confirmed the secret does not exist. Not retried."""


class AuthError(VaultlyError):
    """Backend rejected our credentials. Not retried."""


class TransientError(VaultlyError):
    """Transient backend failure (timeout, 5xx, throttling). Retryable."""
