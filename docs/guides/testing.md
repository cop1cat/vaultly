# Testing your config

Use `MockBackend` for unit and integration tests of code that consumes a
`SecretModel`. It's an in-memory dict that implements the same `Backend`
contract as real ones, with call tracking for assertions.

## Quick example

```python
from vaultly import MockBackend, Secret, SecretModel


class App(SecretModel):
    stage: str
    db_password: str = Secret("/{stage}/db/password")
    api_key: str = Secret("/services/openai/key")


def test_app_uses_correct_paths():
    backend = MockBackend(
        {
            "/prod/db/password": "s3cr3t",
            "/services/openai/key": "sk-test",
        }
    )
    app = App(stage="prod", backend=backend)
    assert app.db_password == "s3cr3t"
    assert app.api_key == "sk-test"
    # MockBackend tracks every call.
    assert backend.calls == [
        ("/prod/db/password", None),
        ("/services/openai/key", None),
    ]
```

## Asserting on cache behavior

`MockBackend.calls` is a list of `(path, version)` tuples. Use it to
assert on caching:

```python
def test_repeated_reads_hit_cache():
    backend = MockBackend({"/prod/db/password": "s3cr3t"})
    app = App(stage="prod", backend=backend)

    _ = app.db_password
    _ = app.db_password
    _ = app.db_password

    # Three reads, one backend call — caching works.
    assert backend.calls == [("/prod/db/password", None)]


def test_refresh_actually_refetches():
    backend = MockBackend({"/prod/db/password": "v1"})
    app = App(stage="prod", backend=backend)
    _ = app.db_password
    backend.reset_calls()

    backend.data["/prod/db/password"] = "v2"
    assert app.refresh("db_password") == "v2"
    assert backend.calls == [("/prod/db/password", None)]
```

## Versioned secrets

Pass a separate `versions=` dict for pinned versions:

```python
backend = MockBackend(
    versions={("/db/password", 2): "older"},
)

class App(SecretModel):
    pinned: str = Secret("/db/password", version=2)

App(backend=backend).pinned == "older"
```

## Testing error paths

`MockBackend` raises `SecretNotFoundError` for missing keys:

```python
import pytest
from vaultly import SecretNotFoundError

def test_missing_secret_raises():
    backend = MockBackend({})
    app = App(stage="prod", backend=backend)
    with pytest.raises(SecretNotFoundError):
        _ = app.db_password
```

For testing retry / stale-on-error scenarios, write a small
fault-injecting `Backend` subclass:

```python
from vaultly import Backend, TransientError


class FlakyBackend(Backend):
    def __init__(self, data, fail_first=0):
        self.data = data
        self.fail_first = fail_first
        self.calls = 0

    def get(self, path, *, version=None):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise TransientError("simulated outage")
        return self.data[path]
```

Then plug it into `RetryingBackend` in your test the same way you would
in production.

## Path-validation tests

Construction performs path validation by default. Catch typos at test
time:

```python
import pytest
from vaultly import MissingContextVariableError


def test_typo_in_path_caught_at_construction():
    class Broken(SecretModel):
        stage: str
        x: str = Secret("/{stge}/x")  # typo

    with pytest.raises(MissingContextVariableError, match="stge"):
        Broken(stage="prod", backend=MockBackend({}))
```

## End-to-end with real backends

For integration tests that exercise the real wire format, use `moto`
for AWS or `hvac.Client` mocked at the SDK level for Vault. See
`tests/integration/` in the vaultly repo for a working example.

```python
from moto import mock_aws
import boto3
from vaultly.backends.aws_ssm import AWSSSMBackend


@mock_aws
def test_with_real_ssm_wire_format():
    boto3.client("ssm").put_parameter(
        Name="/test/key", Value="real", Type="SecureString",
    )
    backend = AWSSSMBackend(region_name="us-east-1")
    assert backend.get("/test/key") == "real"
```

## What NOT to do in tests

- **Don't `model_copy` / `pickle`** test instances. Both are blocked by
  design. Construct fresh instances per test.
- **Don't share `MockBackend` instances across tests** unless you're
  explicitly testing cross-test caching. Each test should own its
  backend so call assertions stay clean.
- **Don't rely on TTL-based timing** in tests with very short TTLs
  (sub-millisecond). Use `MockBackend.reset_calls()` and explicit
  `refresh()` instead — much more reliable than racing `time.sleep`.
