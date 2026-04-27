# SecretModel

`SecretModel` is the base class users inherit from. It's a thin layer on
top of `pydantic.BaseModel` that adds:

- Detection of secret-backed fields declared via `Secret(...)`
- Lazy fetch on first attribute access
- A shared, thread-safe TTL cache
- Masking in `repr` / `model_dump` / JSON
- Path-interpolation validation at construction time

Everything else — field types, validators, `model_config`, inheritance —
behaves exactly like Pydantic. You can mix vaultly fields with regular
Pydantic fields, computed fields, validators, etc.

## Lifecycle

```python
class App(SecretModel):
    stage: str
    db_password: str = Secret("/db/{stage}/password", ttl=60)
```

When you construct `App(stage="prod", backend=...)`:

1. **Pydantic validates the inputs** as usual (`stage` must be `str`).
2. **vaultly's `model_validator(mode='after')` runs**:
    - Wires nested `SecretModel` children to this root (so they share its
      cache and backend).
    - Walks every `Secret(...)`-declared path and verifies that each `{var}`
      resolves to an actual non-secret field on the root model.
    - If `validate="fetch"` is set on the class, calls `prefetch()` to
      eagerly fill the cache from the backend.
3. **Construction returns** — no secret values have been read yet (unless
   you opted into prefetch).

When you access a secret field (`app.db_password`):

1. `SecretModel.__getattribute__` notices `db_password` is a secret field.
2. The path template is filled with the model's current scalar fields.
3. The cache is consulted; on a hit, return immediately.
4. On a miss, a per-key fetch lock is acquired; the backend is called once
   even if 100 threads ask in parallel.
5. The raw string is cast to the field's annotated type (`str`, `int`,
   `bool`, `dict`, `list`, or whatever a custom `transform=` produces).
6. The value is stored in the cache and returned.

## Configuration

Subclass-level config is set via class kwargs (preferred) or via underscored
ClassVars (fallback for older patterns):

=== "Class kwargs (preferred)"

    ```python
    class App(SecretModel, validate="fetch", stale_on_error=True):
        ...
    ```

=== "ClassVar (fallback)"

    ```python
    class App(SecretModel):
        _vaultly_validate = "fetch"
        _vaultly_stale_on_error = True
    ```

### `validate`

What to check at construction time:

- `"none"` — skip everything. Errors surface only on first fetch.
- `"paths"` (default) — verify every `{var}` resolves against the root
  fields. Cheap, catches typos.
- `"fetch"` — additionally call `prefetch()` to read every secret.
  Catches missing secrets / auth issues at startup, at the cost of one
  extra backend round-trip during construction.

### `stale_on_error`

If the backend raises `TransientError` and there's an expired value in the
cache, return that with a warning log instead of raising.

Off by default — for some workloads, returning a stale credential during
an outage is worse than failing. Opt in per model:

```python
class App(SecretModel, stale_on_error=True):
    ...
```

## Public API

| Method                | Purpose                                                |
| --------------------- | ------------------------------------------------------ |
| `prefetch()`          | Eagerly fetch every secret in the tree.                |
| `refresh(name)`       | Invalidate one field's cache entry and re-fetch.       |
| `refresh_all()`       | Invalidate the whole cache.                            |

## What `SecretModel` does NOT support

These deliberately raise `NotImplementedError`:

- `model.model_copy()`
- `copy.copy(model)`
- `copy.deepcopy(model)`
- `pickle.dumps(model)`

All of them would either share or duplicate the in-memory cache (containing
cleartext secret values) and break the parent/root linkage in nested
trees. Construct a fresh instance instead. See the [security model
guide](../guides/security-model.md) for the rationale.

`model.model_construct(...)` is allowed but skips Pydantic's validation
pipeline entirely — including ours. Path validation and prefetch don't
run. Errors surface lazily on first fetch. Use only when you know what
you're doing.

## Public-API surface

```python
from vaultly import (
    SecretModel,         # base class
    Secret,              # field marker
    Backend,             # ABC for custom backends
    EnvBackend,          # built-in: env vars
    MockBackend,         # built-in: in-memory, for tests
    RetryingBackend,     # wrapper: retry on TransientError
    # error types
    VaultlyError,
    ConfigError,
    MissingContextVariableError,
    SecretNotFoundError,
    AuthError,
    TransientError,
)
```

Optional cloud backends live in their own submodules so `import vaultly`
works without their SDKs installed:

```python
from vaultly.backends.aws_ssm import AWSSSMBackend
from vaultly.backends.vault import VaultBackend
```
