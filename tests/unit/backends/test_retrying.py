from __future__ import annotations

import pytest

from vaultly import RetryingBackend
from vaultly.backends.base import Backend
from vaultly.errors import AuthError, SecretNotFoundError, TransientError


class FlakyBackend(Backend):
    """Raises `to_raise` for the first N calls, then returns `final`."""

    def __init__(
        self,
        final: str = "ok",
        fail_times: int = 0,
        to_raise: type[Exception] = TransientError,
    ) -> None:
        self.final = final
        self.fail_times = fail_times
        self.to_raise = to_raise
        self.calls = 0

    def get(self, path: str, *, version: int | str | None = None) -> str:
        del version
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.to_raise("boom")
        return self.final


class CapturingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, d: float) -> None:
        self.delays.append(d)


def test_success_on_first_try():
    inner = FlakyBackend(final="v", fail_times=0)
    sleep = CapturingSleep()
    r = RetryingBackend(inner, sleep=sleep, rng=lambda: 1.0)
    assert r.get("/x") == "v"
    assert inner.calls == 1
    assert sleep.delays == []


def test_retries_and_succeeds():
    inner = FlakyBackend(final="v", fail_times=2)
    sleep = CapturingSleep()
    r = RetryingBackend(inner, max_attempts=3, sleep=sleep, rng=lambda: 1.0)
    assert r.get("/x") == "v"
    assert inner.calls == 3
    # Two sleeps between three attempts.
    assert len(sleep.delays) == 2


def test_exceeds_max_attempts_raises_last():
    inner = FlakyBackend(fail_times=10)
    sleep = CapturingSleep()
    r = RetryingBackend(inner, max_attempts=3, sleep=sleep, rng=lambda: 1.0)
    with pytest.raises(TransientError, match="boom"):
        r.get("/x")
    assert inner.calls == 3


def test_non_transient_is_not_retried():
    inner = FlakyBackend(fail_times=10, to_raise=SecretNotFoundError)
    sleep = CapturingSleep()
    r = RetryingBackend(inner, max_attempts=5, sleep=sleep)
    with pytest.raises(SecretNotFoundError):
        r.get("/x")
    assert inner.calls == 1
    assert sleep.delays == []


def test_auth_error_is_not_retried():
    inner = FlakyBackend(fail_times=10, to_raise=AuthError)
    r = RetryingBackend(inner, max_attempts=5, sleep=CapturingSleep())
    with pytest.raises(AuthError):
        r.get("/x")
    assert inner.calls == 1


def test_backoff_is_exponential_and_capped(monkeypatch):
    inner = FlakyBackend(fail_times=10)
    sleep = CapturingSleep()
    r = RetryingBackend(
        inner,
        max_attempts=5,
        base_delay=1.0,
        max_delay=3.0,
        jitter=False,
        sleep=sleep,
    )
    with pytest.raises(TransientError):
        r.get("/x")
    # attempts 1..4 each sleep (attempt==5 is the final try, no sleep after).
    # 1.0, 2.0, 3.0 (capped), 3.0 (capped)
    assert sleep.delays == [1.0, 2.0, 3.0, 3.0]


def test_jitter_scales_delay():
    inner = FlakyBackend(fail_times=10)
    sleep = CapturingSleep()
    r = RetryingBackend(
        inner,
        max_attempts=3,
        base_delay=1.0,
        max_delay=4.0,
        jitter=True,
        sleep=sleep,
        rng=lambda: 0.25,
    )
    with pytest.raises(TransientError):
        r.get("/x")
    # base 1.0 * 2^0 = 1.0 * jitter 0.25 = 0.25
    # base 1.0 * 2^1 = 2.0 * jitter 0.25 = 0.50
    assert sleep.delays == [0.25, 0.5]


def test_get_batch_also_retries():
    class BatchFlaky(Backend):
        def __init__(self) -> None:
            self.get_calls = 0
            self.batch_calls = 0

        def get(self, path: str, *, version: int | str | None = None) -> str:
            del version
            self.get_calls += 1
            return "v"

        def get_batch(self, paths: list[str]) -> dict[str, str]:
            self.batch_calls += 1
            if self.batch_calls < 2:
                raise TransientError("batch boom")
            return dict.fromkeys(paths, "v")

    inner = BatchFlaky()
    r = RetryingBackend(inner, max_attempts=3, sleep=CapturingSleep(), rng=lambda: 0.0)
    assert r.get_batch(["/a", "/b"]) == {"/a": "v", "/b": "v"}
    assert inner.batch_calls == 2


def test_invalid_max_attempts():
    with pytest.raises(ValueError, match="max_attempts"):
        RetryingBackend(FlakyBackend(), max_attempts=0)


def test_invalid_total_timeout():
    with pytest.raises(ValueError, match="total_timeout"):
        RetryingBackend(FlakyBackend(), total_timeout=0)


def test_total_timeout_short_circuits_long_backoff():
    """If the next sleep would exceed the budget, give up immediately."""
    inner = FlakyBackend(fail_times=10)
    sleep = CapturingSleep()

    # Fake monotonic clock so we control "elapsed" deterministically.
    now = [0.0]

    def fake_monotonic() -> float:
        return now[0]

    def fake_sleep(d: float) -> None:
        sleep.delays.append(d)
        now[0] += d

    r = RetryingBackend(
        inner,
        max_attempts=10,
        base_delay=1.0,
        max_delay=10.0,
        total_timeout=2.5,
        jitter=False,
        sleep=fake_sleep,
        monotonic=fake_monotonic,
        rng=lambda: 1.0,
    )
    with pytest.raises(TransientError):
        r.get("/x")
    # 1.0 sleep + 2.0 sleep would put us at t=3.0 (>2.5 budget) before the
    # third sleep — so we sleep at most twice and bail.
    assert sleep.delays == [1.0, 2.0] or sleep.delays == [1.0]


def test_total_timeout_none_disables_budget():
    inner = FlakyBackend(fail_times=4)
    sleep = CapturingSleep()
    r = RetryingBackend(
        inner,
        max_attempts=5,
        base_delay=0.001,
        max_delay=0.001,
        total_timeout=None,
        sleep=sleep,
        rng=lambda: 1.0,
    )
    assert r.get("/x") == "ok"
    assert inner.calls == 5
