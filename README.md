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

Set on a subclass via class kwargs:

```python
class AppConfig(SecretModel, validate="fetch", stale_on_error=True):
    ...
```

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

## Versioning

Pin a secret to a specific version:

```python
class AppConfig(SecretModel):
    db_password: str = Secret("/db/password", version=2)
```

Versioned and unversioned reads of the same path are cached separately. SSM
forwards the version as `Name=path:N`; Vault as `version=N` to KV v2; other
backends ignore it. `prefetch()` falls back to serial `get` for versioned
secrets (the batch APIs don't support per-path versions).

## Description

Free-text description that surfaces in error messages — useful in big models:

```python
db_password: str = Secret(
    "/db/{stage}/password",
    description="postgres prod credentials",
)
```

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
class AppConfig(SecretModel, stale_on_error=True):
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

## Security model

What vaultly **does** mask:

- `repr(model)`, `str(model)` — secret fields render as `"***"`.
- `model.model_dump()` and `model.model_dump_json()` — same.

What vaultly **does not** mask:

- A secret field accessed directly (`model.db_password`) is a plain `str`.
  `print(model.db_password)`, log lines, exception messages, and template
  expansions can leak it. We chose `str` over `pydantic.SecretStr` so that
  downstream code (DB drivers, HTTP clients) Just Works — but the
  responsibility to not log it is yours.
- `vars(model)` and `model.__dict__` bypass `__getattribute__` and expose
  the internal `MISSING` sentinel for unfetched fields (and fetched values
  are stored in the cache, not in `__dict__`, so you'll always see the
  sentinel there). Use `model.model_dump()` for introspection — it goes
  through the masking serializer.
- `pickle.dumps(model)`, `copy.copy(model)`, `copy.deepcopy(model)`, and
  `model.model_copy()` all raise `NotImplementedError`. Each would either
  share or duplicate the in-memory cleartext cache and break nested-root
  linkage. Construct a fresh instance instead.
- Process memory — secret strings are not zeroed when evicted; this is
  Python's general posture and would require C-extensions to fix.
- Logger output — `vaultly` uses the `vaultly` logger and emits paths (not
  values) at WARNING level for stale-on-error and retries. Resolved paths
  may contain context like `{stage}` / tenant id; configure your logger
  if you treat those as PII:

  ```python
  import logging
  logging.getLogger("vaultly").addFilter(my_pii_scrubber)
  ```

## Concurrency

- Cache reads and writes are protected by a per-cache `threading.Lock`.
- Cold-cache fetches are serialized per resolved key — N threads asking
  for the same uncached secret produce exactly one backend call.
- Hot-path reads (cache hit) take only the cache lock and return without
  touching the per-key fetch lock.
- Async is not yet supported (planned for v0.2). Today, fetches inside an
  event loop will block; wrap calls in `asyncio.to_thread` if needed.

## Construction paths

vaultly's path validation, `_root` wiring, and optional `prefetch` run via
a Pydantic `model_validator(mode='after')`, which fires for both:

- `AppConfig(stage="prod", backend=...)` (calls `__init__` then validators)
- `AppConfig.model_validate({...})` and `.model_validate_json(...)` (skip
  `__init__`, go straight to validators)

`AppConfig.model_construct(...)` skips all validation by Pydantic design.
You'll get an instance back, but path checks and prefetch don't run; the
first attribute access surfaces any errors lazily.

## Breaking-change policy (pre-1.0)

Before 1.0, the public API may change between minor versions. The
`Backend.get(path, *, version=None)` signature in particular is a candidate
for revision (a future `SecretQuery`-shaped argument is on the table).
Pin the patch version in production until 1.0.
