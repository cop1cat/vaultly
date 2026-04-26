"""Environment-variable backend.

Paths are mapped to env var names by stripping leading/trailing slashes,
replacing inner slashes with underscores, and uppercasing. An optional
`prefix` is prepended; a single underscore is auto-inserted between prefix
and key unless the prefix already ends with one.

    /db/prod/password                  -> DB_PROD_PASSWORD
    EnvBackend(prefix="MYAPP")  + /key -> MYAPP_KEY
    EnvBackend(prefix="MYAPP_") + /key -> MYAPP_KEY
"""

from __future__ import annotations

import os

from vaultly.backends.base import Backend
from vaultly.errors import SecretNotFoundError


class EnvBackend(Backend):
    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix

    def get(self, path: str, *, version: int | str | None = None) -> str:
        # Env vars don't have versions; silently ignore the parameter.
        del version
        key = self._to_env_key(path)
        value = os.environ.get(key)
        if value is None:
            msg = f"environment variable not set: {key}"
            raise SecretNotFoundError(msg)
        return value

    def _to_env_key(self, path: str) -> str:
        base = path.strip("/").replace("/", "_").upper()
        if not self.prefix:
            return base
        if self.prefix.endswith("_"):
            return self.prefix + base
        return f"{self.prefix}_{base}"
