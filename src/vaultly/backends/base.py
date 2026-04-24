"""Backend abstract base.

Concrete backends implement `get(path) -> str`. `get_batch` has a default
implementation that issues N serial `get` calls; backends with a real batch
API (SSM's `GetParameters`, Vault's list+read, etc.) should override it.

Backends are responsible for mapping their SDK exceptions into the vaultly
hierarchy (`SecretNotFoundError`, `AuthError`, `TransientError`). Transport-
level retries (connection resets, DNS, etc.) should stay inside the SDK's own
retry config; semantic retries of `TransientError` are layered on top via
`RetryingBackend`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Backend(ABC):
    @abstractmethod
    def get(self, path: str) -> str:
        """Return the raw string at `path` or raise a vaultly error."""

    def get_batch(self, paths: list[str]) -> dict[str, str]:
        """Fetch many paths at once. Default: serial `get` calls."""
        return {p: self.get(p) for p in paths}
