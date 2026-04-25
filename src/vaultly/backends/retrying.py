"""Retry wrapper for a Backend.

Retries only `TransientError` — `SecretNotFoundError` and `AuthError` are
programmer/config issues, not worth hammering. Uses exponential backoff with
optional jitter; a default total budget of ~10s across the configured number
of attempts protects against slow-starts during outages.

Transport-level retries (DNS, TCP resets, HTTP 5xx) should still be handled
inside each backend's SDK (boto3 Config, hvac adapter, etc.). `RetryingBackend`
is strictly a semantic layer on top: retry once we've *already* translated a
backend failure into `TransientError`.
"""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING

from vaultly.backends.base import Backend
from vaultly.errors import TransientError

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("vaultly")


class RetryingBackend(Backend):
    def __init__(
        self,
        inner: Backend,
        *,
        max_attempts: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 4.0,
        jitter: bool = True,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if max_attempts < 1:
            msg = "max_attempts must be >= 1"
            raise ValueError(msg)
        self.inner = inner
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self._sleep = sleep
        self._rng = rng

    def get(self, path: str) -> str:
        return self._retry(self.inner.get, (path,), path)

    def get_batch(self, paths: list[str]) -> dict[str, str]:
        return self._retry(self.inner.get_batch, (paths,), repr(paths))

    def _retry(self, fn, args, label):  # noqa: ANN001, ANN202  — internal helper
        last: TransientError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return fn(*args)
            except TransientError as e:
                last = e
                if attempt == self.max_attempts:
                    break
                delay = self._compute_delay(attempt)
                logger.warning(
                    "vaultly: transient error fetching %s (attempt %d/%d), "
                    "retrying in %.2fs: %s",
                    label,
                    attempt,
                    self.max_attempts,
                    delay,
                    e,
                )
                self._sleep(delay)
        assert last is not None  # loop always assigns before break
        raise last

    def _compute_delay(self, attempt: int) -> float:
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        if self.jitter:
            # Full jitter: uniform(0, delay). Avoids thundering herd when
            # multiple clients retry in sync.
            delay *= self._rng()
        return delay
