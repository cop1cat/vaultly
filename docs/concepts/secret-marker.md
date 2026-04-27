# `Secret(...)`

`Secret(...)` is the marker that makes a field secret-backed. It's used
in the right-hand side of a field declaration:

```python
from vaultly import Secret, SecretModel


class App(SecretModel):
    db_password: str = Secret("/db/password", ttl=60)
```

It returns a Pydantic `FieldInfo` with a sentinel default, so the field is
optional at construction time. The `path`, `ttl`, `transform`, `version`,
and `description` you pass are stored as metadata that the model reads
at class-creation time.

## Parameters

```python
def Secret(
    path: str,
    *,
    ttl: float | None = None,
    transform: Callable[[str], Any] | None = None,
    version: int | str | None = None,
    description: str | None = None,
) -> Any: ...
```

### `path`

The backend path. `{var}` placeholders are filled at fetch time from
non-secret fields of the **root** `SecretModel`.

```python
db_password: str = Secret("/{stage}/db/password")
```

Path interpolation is detailed in [Path interpolation](path-interpolation.md).

### `ttl`

How long the resolved value lives in the per-root cache:

| Value         | Meaning                                           |
| ------------- | ------------------------------------------------- |
| `None` (default) | Cache forever (no expiration).                 |
| `0`              | Never cache — every access calls the backend.  |
| `> 0`            | Number of seconds.                              |

Each path keeps its own TTL. Versioned secrets are cached separately from
the unversioned form of the same path.

### `transform`

A custom `Callable[[str], T]` that overrides vaultly's default cast rules
entirely:

```python
import json
import base64

class App(SecretModel):
    # decode a base64-encoded JSON document into a Python object
    config: dict = Secret(
        "/app/config-b64",
        transform=lambda s: json.loads(base64.b64decode(s)),
    )
```

If `transform` is provided, the field's annotation does not drive any
casting — your callable is fully responsible. Exceptions raised by
`transform` are wrapped as `ConfigError` with the original as `__cause__`.

### `version`

Pin the secret to a specific version. Backends that support versioning
forward this as appropriate:

| Backend           | Wire format                          |
| ----------------- | ------------------------------------ |
| `AWSSSMBackend`   | Appends `:N` to the parameter name.  |
| `VaultBackend`    | Passes `version=N` to KV v2.         |
| `EnvBackend`      | Ignored (env vars don't version).    |

Versioned and unversioned reads of the same path are cached separately.

### `description`

Free-text description. Surfaces in error messages — useful for big models
where `MissingContextVariableError: secret field App.db (postgres prod
credentials)` is more debuggable than `MissingContextVariableError:
secret field App.db`.

## What `Secret(...)` does NOT support

- `Secret` is **not** a Python type. You cannot write `db_password:
  Secret(str)` or `db_password: Secret[str]`. The shape is always
  `field_name: PythonType = Secret("/path", ...)`.
- It's not a Pydantic alias. Other Pydantic field metadata (`Field(min_length=...)`,
  validators) doesn't combine with `Secret` — vaultly owns the field's
  serialization and validation behavior.
- Multiple secrets per field aren't supported. One `Secret(...)` per field.

## Cast rules (default)

When `transform` isn't given, the field's annotation drives the cast:

| Annotation              | Cast                                                       |
| ----------------------- | ---------------------------------------------------------- |
| `str`                   | passthrough                                                |
| `int` / `float`         | direct                                                     |
| `bool`                  | `true/1/yes/on` ↔ `false/0/no/off` (case-insensitive)      |
| `dict` / `list`         | `json.loads`                                               |
| `T \| None` (Optional)  | unwrapped to `T`, then the rule for `T` applies            |
| anything else           | raw string                                                 |

A cast failure raises `ConfigError`, never the underlying exception type
(`ValueError`, `JSONDecodeError`, etc.). Code that does
`except VaultlyError` will reliably catch it.
