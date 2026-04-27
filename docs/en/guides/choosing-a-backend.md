# Choosing a backend

Quick decision tree:

```text
┌─ Is this a unit/integration test?
│  └─ MockBackend  (in-memory dict, tracks calls)
│
├─ Local dev / simple container deployment / CI?
│  └─ EnvBackend   (env vars, optional prefix)
│
├─ Running on AWS, secrets in SSM Parameter Store?
│  └─ AWSSSMBackend  (likely wrapped in RetryingBackend)
│
├─ HashiCorp Vault for everything?
│  └─ VaultBackend (likely wrapped in RetryingBackend, with token_factory)
│
└─ Something else (Azure KV, GCP SM, custom)?
   └─ Subclass Backend, ~30 lines of code (see Backends concept)
```

## EnvBackend

The lowest-friction option. Maps `/db/prod/password` → `DB_PROD_PASSWORD`.

```python
from vaultly import EnvBackend

backend = EnvBackend()                  # no prefix
backend = EnvBackend(prefix="MYAPP")    # MYAPP_DB_PROD_PASSWORD
backend = EnvBackend(prefix="MYAPP_")   # MYAPP_DB_PROD_PASSWORD (auto-de-dup)
```

A single underscore is auto-inserted between prefix and key unless your
prefix already ends with one.

**Don't use** for production-grade secrets. Env vars are visible to
anyone with `/proc/<pid>/environ` access; for real secrets, use a dedicated
secret store.

## MockBackend

For tests. Construct with a path → value dict. Tracks every call so you
can assert on cache behavior.

```python
from vaultly import MockBackend

b = MockBackend({"/db/password": "s3cr3t", "/api/key": "sk"})
config = AppConfig(stage="prod", backend=b)
config.db_password         # "s3cr3t"
b.calls                    # [("/db/password", None)]
```

For versioned secrets, pass a separate `versions=` dict:

```python
b = MockBackend(versions={("/db/password", 2): "older"})
```

`MockBackend` raises `SecretNotFoundError` for missing keys, matching the
contract of real backends — so error-path tests work the same way.

## AWSSSMBackend

```python
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = AWSSSMBackend(region_name="eu-west-1")
```

By default, ships with sensible production timeouts (2s connect / 5s read)
and adaptive retries. Pass `config=` to override:

```python
from botocore.config import Config

backend = AWSSSMBackend(
    region_name="eu-west-1",
    config=Config(
        retries={"mode": "standard", "max_attempts": 5},
        connect_timeout=1.0,
        read_timeout=3.0,
    ),
)
```

See [AWS SSM guide](aws-ssm.md) for the full feature matrix (SecureString,
batched reads, versioning).

## VaultBackend

```python
from vaultly.backends.vault import VaultBackend

backend = VaultBackend(
    url="https://vault.example.com",
    token=os.environ["VAULT_TOKEN"],
    mount_point="secret",        # KV v2 mount point (default)
    default_key="value",         # field within secret dict (default)
)
```

For short-lived tokens (AppRole, K8s auth), pass a `token_factory=`
callable that returns a fresh token. vaultly invokes it once on
`Unauthorized` and retries the read.

See [Vault guide](vault.md) for `path:key` syntax, KV v2 specifics, and
token renewal patterns.

## RetryingBackend

Wraps any other backend. Retries only `TransientError` (timeouts,
throttling, 5xx). Auth and not-found errors are not retried.

```python
from vaultly import RetryingBackend
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = RetryingBackend(
    AWSSSMBackend(region_name="eu-west-1"),
    max_attempts=3,
    base_delay=0.5,
    max_delay=4.0,
    total_timeout=10.0,
)
```

`total_timeout` is a hard wall-clock budget — even if `max_attempts` would
allow more, vaultly stops retrying once the budget is exhausted. This
prevents a 30-minute outage from spawning a 30-minute startup hang.

See [Retries and stale-on-error guide](retries-and-stale.md) for the
interaction between retries, TTL, and the `stale_on_error` model option.
