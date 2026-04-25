"""Mock backend for tests.

Tracks every fetched (path, version) tuple in `calls` so tests can assert
on N-calls, ordering, and cache-hit behavior. Raises `SecretNotFoundError`
for missing keys, matching the contract of real backends.

Versioned values can be supplied via `versions={(path, version): value}`;
unversioned values via `data={path: value}`.
"""

from __future__ import annotations

from vaultly.backends.base import Backend
from vaultly.errors import SecretNotFoundError


class MockBackend(Backend):
    def __init__(
        self,
        data: dict[str, str] | None = None,
        *,
        versions: dict[tuple[str, int | str], str] | None = None,
    ) -> None:
        self.data: dict[str, str] = dict(data) if data else {}
        self.versions: dict[tuple[str, int | str], str] = (
            dict(versions) if versions else {}
        )
        self.calls: list[tuple[str, int | str | None]] = []

    def get(self, path: str, *, version: int | str | None = None) -> str:
        self.calls.append((path, version))
        if version is not None:
            try:
                return self.versions[path, version]
            except KeyError as e:
                msg = f"mock backend has no value for: {path}@{version}"
                raise SecretNotFoundError(msg) from e
        try:
            return self.data[path]
        except KeyError as e:
            msg = f"mock backend has no value for: {path}"
            raise SecretNotFoundError(msg) from e

    def reset_calls(self) -> None:
        self.calls.clear()
