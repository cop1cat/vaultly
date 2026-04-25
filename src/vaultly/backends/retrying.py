"""Retry wrapper for a Backend.

Retries only `TransientError` — `SecretNotFoundError` and `AuthError` are
programmer/config issues, not worth hammering. Uses exponential backoff with
optional jitter and a hard wall-clock budget (`total_timeout`) so an outage
can't turn into a multi-minute pause.

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
        total_timeout: float | None = 10.0,
        jitter: bool = True,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_attempts < 1:
            msg = "max_attempts must be >= 1"
            raise ValueError(msg)
        if total_timeout is not None and total_timeout <= 0:
            msg = "total_timeout must be positive or None"
            raise ValueError(msg)
        self.inner = inner
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.total_timeout = total_timeout
        self.jitter = jitter
        self._sleep = sleep
        self._rng = rng
        self._monotonic = monotonic

    def get(self, path: str, *, version: int | str | None = None) -> str:
        label = f"{path}@{version}" if version is not None else path
        return self._retry(
            lambda: self.inner.get(path, version=version),
            label,
        )

    def get_batch(self, paths: list[str]) -> dict[str, str]:
        return self._retry(lambda: self.inner.get_batch(paths), repr(paths))

    def _retry(self, fn, label):  # noqa: ANN001, ANN202  — internal helper
        deadline = (
            self._monotonic() + self.total_timeout
            if self.total_timeout is not None
            else None
        )
        last: TransientError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return fn()
            except TransientError as e:
                last = e
                if attempt == self.max_attempts:
                    break
                delay = self._compute_delay(attempt)
                if deadline is not None and self._monotonic() + delay >= deadline:
                    # Sleeping would exceed our wall-clock budget — give up
                    # rather than burn the rest of the budget on a sleep.
                    logger.warning(
                        "vaultly: total_timeout reached after %d attempt(s) for %s",
                        attempt,
                        label,
                    )
                    break
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
