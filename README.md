# vaultly

Declarative, Pydantic-native secrets manager for Python 3.12+.

Mix regular Pydantic fields with secret fields in one model. Secrets are
fetched lazily on first access, cached with per-field TTL, masked in `repr`
and `model_dump`, and never carry a different type than the one you
declared:

```python
from vaultly import Secret, SecretModel
from vaultly.backends.aws_ssm import AWSSSMBackend


class AppConfig(SecretModel):
    stage: str = "dev"
    debug: bool = False
    db_password: str = Secret("/db/{stage}/password", ttl=300)
    api_key: str = Secret("/services/openai/key")
    max_conns: int = Secret("/db/{stage}/max_conns")


config = AppConfig(stage="prod", backend=AWSSSMBackend(region_name="eu-west-1"))

config.db_password   # -> str, fetched on first access, cached for 300s
config.max_conns     # -> int, cast from "42"
config.model_dump()  # -> {..., "db_password": "***", "api_key": "***"}
```

## Install

```sh
pip install vaultly                 # core (env / mock backends)
pip install 'vaultly[aws]'          # + AWS Systems Manager Parameter Store
pip install 'vaultly[vault]'        # + HashiCorp Vault (KV v2)
```

## Backends

| Backend            | Import                                   | Notes                              |
| ------------------ | ---------------------------------------- | ---------------------------------- |
| `EnvBackend`       | `from vaultly import EnvBackend`         | env vars; prefix optional          |
| `MockBackend`      | `from vaultly.testing.mock import ...`   | for tests; tracks call list        |
| `AWSSSMBackend`    | `from vaultly.backends.aws_ssm import …` | batched via `GetParameters`        |
| `VaultBackend`     | `from vaultly.backends.vault import …`   | KV v2; `path:key` selects a field  |
| `RetryingBackend`  | `from vaultly import RetryingBackend`    | wraps any backend, retries `TransientError` only |

Backends implement a tiny `Backend` ABC with `get(path) -> str` and
`get_batch(paths) -> dict`. Bring your own by subclassing.

## Path interpolation

`{var}` placeholders in a secret path are filled from non-secret fields of
the **root** model:

```python
db_password: str = Secret("/db/{stage}/password")
```

Nested `SecretModel` fields share the root's context, backend, and cache —
they never resolve `{var}` against their own fields.

Path validation runs at construction time. A typo (`{stge}` instead of
`{stage}`) fails immediately with `MissingContextVariableError` instead of
six hours later in production.

## Casts

The annotated field type drives the cast:

| Annotation       | Cast                                           |
| ---------------- | ---------------------------------------------- |
| `str`            | passthrough                                    |
| `int` / `float`  | direct                                         |
| `bool`           | `true/1/yes/on` ↔ `false/0/no/off` (case-insensitive) |
| `dict` / `list`  | `json.loads`                                   |

Custom: `Secret("/x", transform=...)` replaces the default rule entirely.

## Validation modes

Set on a subclass via `_vaultly_validate`:

* `"paths"` (default) — verify every `{var}` resolves at construction.
* `"fetch"` — additionally `prefetch()` everything via `backend.get_batch`
  at construction. Fails fast at startup if any secret is missing.
* `"none"` — skip both. Errors surface on first access.

Manual control:

```python
config.prefetch()         # fetch the whole tree now (uses get_batch)
config.refresh("api_key") # invalidate one and re-fetch
config.refresh_all()      # invalidate the whole cache
```

## TTL

`ttl=` on `Secret(...)`:

* `None` (default) — cache forever.
* `0` — never cache; every access hits the backend.
* `> 0` — seconds.

## Retries

```python
from vaultly import RetryingBackend
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = RetryingBackend(
    AWSSSMBackend(region_name="eu-west-1"),
    max_attempts=3,
    base_delay=0.5,
    max_delay=4.0,
)
```

Only `TransientError` is retried (timeouts, throttling, 5xx). Auth and
not-found errors are not. Transport-level retries (DNS, TCP resets) stay
inside each SDK's own retry config — `RetryingBackend` is strictly the
semantic layer on top.

## stale_on_error

Opt in per model. When the backend raises `TransientError` and the cache
holds an expired value, return that with a warning log instead of failing:

```python
class AppConfig(SecretModel):
    _vaultly_stale_on_error = True
    db_password: str = Secret("/db/password", ttl=60)
```

Default is off — for some deployments, returning a stale credential during
an outage is worse than a hard failure.

## Type checking

`pyright` works out of the box. `mypy` users should enable the Pydantic
plugin:

```toml
# pyproject.toml
[tool.mypy]
plugins = ["pydantic.mypy"]
```

A type checker sees `db_password: str` as plain `str`, both at the field
declaration and at the access site.

## Errors

```
VaultlyError
├── ConfigError
│   └── MissingContextVariableError
├── SecretNotFoundError       # not retried
├── AuthError                 # not retried
└── TransientError            # retried by RetryingBackend
```

Each backend maps SDK exceptions into this hierarchy.
