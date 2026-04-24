"""Mock backend for tests.

Tracks every fetched path in `calls` so tests can assert on N-calls,
ordering, and cache-hit behavior. Raises `SecretNotFoundError` for missing
keys, matching the contract of real backends.
"""

from __future__ import annotations

from vaultly.backends.base import Backend
from vaultly.errors import SecretNotFoundError


class MockBackend(Backend):
    def __init__(self, data: dict[str, str] | None = None) -> None:
        self.data: dict[str, str] = dict(data) if data else {}
        self.calls: list[str] = []

    def get(self, path: str) -> str:
        self.calls.append(path)
        try:
            return self.data[path]
        except KeyError as e:
            msg = f"mock backend has no value for: {path}"
            raise SecretNotFoundError(msg) from e

    def reset_calls(self) -> None:
        self.calls.clear()
