"""Retry wrapper for a Backend.

By default retries only `TransientError` — `SecretNotFoundError` and
`AuthError` are programmer/config issues, not worth hammering. The defaults
use exponential backoff with full jitter and a hard wall-clock budget
(`total_timeout`) so an outage can't turn into a multi-minute pause.

For finer control, three callbacks let you plug in your own logic:

* `is_retryable(exc) -> bool` — decide which exceptions trigger a retry.
* `backoff(attempt) -> float` — compute the delay before attempt N+1.
* `on_retry(attempt, exc, delay)` — fired before each sleep (use it to
  emit metrics, breadcrumbs, structured logs).

Transport-level retries (DNS, TCP resets, HTTP 5xx) should still be handled
inside each backend's SDK (boto3 Config, hvac adapter, etc.). `RetryingBackend`
is strictly a semantic layer on top.
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


def _default_is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, TransientError)


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
        is_retryable: Callable[[BaseException], bool] | None = None,
        backoff: Callable[[int], float] | None = None,
        on_retry: Callable[[int, BaseException, float], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wrap `inner` with retry semantics.

        Args:
            inner: The backend to delegate to.
            max_attempts: Cap on total tries (>= 1).
            base_delay: Starting delay for the default exponential schedule.
            max_delay: Cap for the default exponential schedule.
            total_timeout: Wall-clock budget across all attempts (seconds).
                `None` disables; otherwise must be > 0.
            jitter: Apply full jitter to the default schedule. Ignored when
                a custom `backoff=` is given.
            is_retryable: Predicate `(exc) -> bool` deciding whether to
                retry. Defaults to "retry only TransientError". You'd
                widen this if your backend reasonably retries other error
                types (e.g. eventual-consistency `SecretNotFoundError` on
                a freshly written key).
            backoff: Custom delay function `(attempt) -> seconds`. `attempt`
                is 1-based. When given, replaces the default
                `base_delay x 2^(attempt-1)`-with-jitter formula entirely.
            on_retry: Callback fired before each retry sleep, with
                `(attempt, exc, delay)`. Use it to record metrics or push
                breadcrumbs. Runs synchronously; keep it cheap.
            sleep: Override-point for tests; replaces `time.sleep`.
            rng: Override-point for tests; replaces `random.random`.
            monotonic: Override-point for tests; replaces `time.monotonic`.

        Raises:
            ValueError: If `max_attempts < 1` or `total_timeout <= 0`.
        """
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
        self._is_retryable = is_retryable or _default_is_retryable
        self._backoff = backoff
        self._on_retry = on_retry
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
        last: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return fn()
            except Exception as e:
                if not self._is_retryable(e):
                    raise
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
                if self._on_retry is not None:
                    try:
                        self._on_retry(attempt, e, delay)
                    except Exception:
                        # A buggy on_retry must NOT break the retry loop;
                        # log and keep going.
                        logger.exception("vaultly: on_retry callback raised")
                self._sleep(delay)
        assert last is not None  # loop always assigns before break
        raise last

    def _compute_delay(self, attempt: int) -> float:
        if self._backoff is not None:
            return self._backoff(attempt)
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        if self.jitter:
            # Full jitter: uniform(0, delay). Avoids thundering herd when
            # multiple clients retry in sync.
            delay *= self._rng()
        return delay
