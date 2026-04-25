"""Backend abstract base.

Concrete backends implement `get(path, *, version=None) -> str`. `get_batch`
has a default implementation that issues N serial `get` calls; backends with
a real batch API (SSM's `GetParameters`, Vault's list+read, etc.) should
override it. Note that `get_batch` does *not* take a per-path version —
versioned fetches always go through serial `get` calls.

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
    def get(self, path: str, *, version: int | str | None = None) -> str:
        """Return the raw string at `path` or raise a vaultly error.

        `version`, if given, pins to a specific version of the secret.
        Backends that don't support versioning should ignore it.
        """

    def get_batch(self, paths: list[str]) -> dict[str, str]:
        """Fetch many paths at once (latest versions). Default: serial `get`.

        Duplicates in `paths` are collapsed before fetching — backends with
        a real batch API (e.g. SSM `GetParameters`) reject duplicate names,
        and a single shared cache key suffices.
        """
        out: dict[str, str] = {}
        for p in paths:
            if p not in out:
                out[p] = self.get(p)
        return out
