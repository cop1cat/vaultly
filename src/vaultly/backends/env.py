"""Environment-variable backend.

Paths are mapped to env var names by stripping leading/trailing slashes,
replacing inner slashes with underscores, and uppercasing. An optional
`prefix` is prepended verbatim.

    /db/prod/password                  -> DB_PROD_PASSWORD
    EnvBackend(prefix="MYAPP_") + /key -> MYAPP_KEY
"""

from __future__ import annotations

import os

from vaultly.backends.base import Backend
from vaultly.errors import SecretNotFoundError


class EnvBackend(Backend):
    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix

    def get(self, path: str) -> str:
        key = self._to_env_key(path)
        value = os.environ.get(key)
        if value is None:
            msg = f"environment variable not set: {key}"
            raise SecretNotFoundError(msg)
        return value

    def _to_env_key(self, path: str) -> str:
        return self.prefix + path.strip("/").replace("/", "_").upper()
