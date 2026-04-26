"""End-to-end AWS SSM Parameter Store via moto.

Goes beyond the unit tests in `tests/backends/test_aws_ssm.py`:
- Constructs a full SecretModel against real moto-backed SSM.
- Verifies SecureString decryption flow.
- Tests batched fetch where the batch crosses the 10-param SSM limit.
- Tests rotation: change a parameter via boto3, refresh through vaultly.
- Tests RetryingBackend over SSM under simulated throttling.
"""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws
import pytest

from vaultly import RetryingBackend, Secret, SecretModel
from vaultly.backends.aws_ssm import AWSSSMBackend
from vaultly.errors import SecretNotFoundError, TransientError


def _put(name: str, value: str, *, secure: bool = True) -> None:
    boto3.client("ssm").put_parameter(
        Name=name,
        Value=value,
        Type="SecureString" if secure else "String",
        Overwrite=True,
    )


# --------------------------------------------------------------------------- realistic shape via moto


@mock_aws
def test_full_app_via_real_moto_ssm() -> None:
    _put("/prod/db/password", "real-pw")
    _put("/prod/db/pool", "30", secure=False)
    _put("/prod/api/key", "sk-prod")
    _put("/prod/flags", '{"a": 1, "b": false}', secure=False)

    class App(SecretModel):
        stage: str
        db_password: str = Secret("/{stage}/db/password")
        pool_size: int = Secret("/{stage}/db/pool")
        api_key: str = Secret("/{stage}/api/key")
        flags: dict = Secret("/{stage}/flags")

    app = App(stage="prod", backend=AWSSSMBackend(region_name="us-east-1"))

    assert app.db_password == "real-pw"
    assert app.pool_size == 30
    assert isinstance(app.pool_size, int)
    assert app.api_key == "sk-prod"
    assert app.flags == {"a": 1, "b": False}


@mock_aws
def test_validate_fetch_uses_get_parameters_batch() -> None:
    """Prefetch via SSM should hit GetParameters once with all paths."""
    for i in range(5):
        _put(f"/prod/k{i}", f"v{i}", secure=False)

    class App(SecretModel, validate="fetch"):
        a: str = Secret("/prod/k0")
        b: str = Secret("/prod/k1")
        c: str = Secret("/prod/k2")
        d: str = Secret("/prod/k3")
        e: str = Secret("/prod/k4")

    backend = AWSSSMBackend(region_name="us-east-1")

    # Spy on GetParameters
    real = backend._client.get_parameters
    calls: list[list[str]] = []

    def spy(**kw):
        calls.append(list(kw["Names"]))
        return real(**kw)

    object.__setattr__(backend._client, "get_parameters", spy)

    app = App(backend=backend)
    assert app.a == "v0"
    assert app.e == "v4"

    # One batched call (≤10 params).
    assert len(calls) == 1
    assert sorted(calls[0]) == sorted(
        [f"/prod/k{i}" for i in range(5)]
    )


@mock_aws
def test_batch_above_ssm_limit_chunks_correctly() -> None:
    """SSM caps GetParameters at 10 names. 15 params must be 2 calls."""
    for i in range(15):
        _put(f"/prod/k{i:02d}", f"v{i}", secure=False)

    class App(SecretModel, validate="fetch"):
        pass

    # Build 15 secret fields dynamically.
    annotations: dict[str, type] = {}
    body: dict[str, object] = {}
    for i in range(15):
        annotations[f"k{i:02d}"] = str
        body[f"k{i:02d}"] = Secret(f"/prod/k{i:02d}")
    body["__annotations__"] = annotations

    Big = type("Big", (SecretModel,), body, validate="fetch")

    backend = AWSSSMBackend(region_name="us-east-1")
    real = backend._client.get_parameters
    calls: list[list[str]] = []

    def spy(**kw):
        calls.append(list(kw["Names"]))
        return real(**kw)

    object.__setattr__(backend._client, "get_parameters", spy)

    Big(backend=backend)
    assert len(calls) == 2  # 10 + 5
    assert sorted([len(c) for c in calls]) == [5, 10]


@mock_aws
def test_rotation_via_boto_then_refresh_via_vaultly() -> None:
    _put("/prod/db/password", "v1")

    class App(SecretModel):
        pw: str = Secret("/prod/db/password")

    app = App(backend=AWSSSMBackend(region_name="us-east-1"))
    assert app.pw == "v1"

    # External rotation (e.g. another tool / operator).
    _put("/prod/db/password", "v2")

    # Cache still has v1 — that's the contract.
    assert app.pw == "v1"

    # Explicit refresh picks up the new value.
    assert app.refresh("pw") == "v2"
    assert app.pw == "v2"


@mock_aws
def test_missing_param_raises_secret_not_found() -> None:
    class App(SecretModel):
        pw: str = Secret("/does/not/exist")

    app = App(backend=AWSSSMBackend(region_name="us-east-1"))
    with pytest.raises(SecretNotFoundError, match="does/not/exist"):
        _ = app.pw


@mock_aws
def test_securestring_value_round_trips() -> None:
    """SSM SecureString uses KMS; with WithDecryption=True we get plaintext back."""
    secret = "p@ssw0rd-with-$pec!al-chars"
    _put("/prod/db/pw", secret)

    class App(SecretModel):
        pw: str = Secret("/prod/db/pw")

    app = App(backend=AWSSSMBackend(region_name="us-east-1"))
    assert app.pw == secret


# --------------------------------------------------------------------------- retry over real SSM


class FlakySSMWrapper:
    """Wraps a real SSM client and injects throttling for the first N calls."""

    def __init__(self, real_client: object, fail_first: int) -> None:
        self._real = real_client
        self._left = fail_first
        self.real_calls = 0

    def get_parameter(self, **kw: object) -> dict:
        self.real_calls += 1
        if self._left > 0:
            self._left -= 1
            raise ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "slow"}},
                "GetParameter",
            )
        return self._real.get_parameter(**kw)  # type: ignore[attr-defined]

    def get_parameters(self, **kw: object) -> dict:
        self.real_calls += 1
        if self._left > 0:
            self._left -= 1
            raise ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "slow"}},
                "GetParameters",
            )
        return self._real.get_parameters(**kw)  # type: ignore[attr-defined]


@mock_aws
def test_retrying_backend_over_ssm_recovers_from_throttling() -> None:
    _put("/prod/api/key", "sk-prod")

    real = boto3.client("ssm", region_name="us-east-1")
    flaky = FlakySSMWrapper(real, fail_first=2)
    inner = AWSSSMBackend(client=flaky)
    backend = RetryingBackend(
        inner,
        max_attempts=5,
        base_delay=0.001,
        max_delay=0.01,
        jitter=False,
        sleep=lambda _d: None,  # no real sleep in tests
    )

    class App(SecretModel):
        api_key: str = Secret("/prod/api/key")

    app = App(backend=backend)
    assert app.api_key == "sk-prod"
    # 2 throttles + 1 success = 3 underlying calls
    assert flaky.real_calls == 3


@mock_aws
def test_retrying_backend_gives_up_after_total_timeout() -> None:
    _put("/prod/api/key", "sk-prod")

    real = boto3.client("ssm", region_name="us-east-1")
    # Throttle forever.
    flaky = FlakySSMWrapper(real, fail_first=10_000)
    inner = AWSSSMBackend(client=flaky)
    # base_delay 1s, total budget 0.5s → can't even sleep once.
    delays: list[float] = []
    backend = RetryingBackend(
        inner,
        max_attempts=10,
        base_delay=1.0,
        max_delay=4.0,
        total_timeout=0.5,
        jitter=False,
        sleep=lambda d: delays.append(d),
        monotonic=_StepClock(0.0).now,
    )

    class App(SecretModel):
        api_key: str = Secret("/prod/api/key")

    app = App(backend=backend)
    with pytest.raises(TransientError):
        _ = app.api_key


class _StepClock:
    """Monotonic clock that advances 1s with each call. Lets us control budget
    consumption deterministically."""

    def __init__(self, start: float) -> None:
        self._t = start

    def now(self) -> float:
        cur = self._t
        self._t += 1.0
        return cur
