# Errors

vaultly raises a small, focused exception hierarchy. Every error a backend
can produce maps to one of these — including SDK-specific ones (boto3
`ClientError`, hvac `VaultError`).

```text
VaultlyError
├── ConfigError
│   └── MissingContextVariableError
├── SecretNotFoundError       # not retried
├── AuthError                 # not retried
└── TransientError            # retried by RetryingBackend
```

## Catching errors

The umbrella base is what most application code wants:

```python
from vaultly import VaultlyError

try:
    config.db_password
except VaultlyError as e:
    log.error("secrets failed: %s", e)
    raise
```

For finer control, catch the subclasses you care about:

```python
from vaultly import AuthError, SecretNotFoundError, TransientError

try:
    config.db_password
except AuthError:
    # credentials problem — alert ops
    ...
except SecretNotFoundError:
    # config-time bug — log and crash
    ...
except TransientError:
    # backend hiccup — RetryingBackend already exhausted
    ...
```

## Each error in detail

### `VaultlyError`

Base class for every exception vaultly raises. Inherits from `Exception`.
Catch this if you want a single block that handles all secret-loading
failures.

### `ConfigError`

Wraps anything that's a programmer/config issue rather than a backend
problem:

- Cast failure: `int("not-a-number")`, `json.loads("{")` (the underlying
  `ValueError` / `JSONDecodeError` is preserved as `__cause__`)
- A `transform=...` callable that raised
- `prefetch()` / `_fetch` called when no `backend=` was provided

Not retried.

### `MissingContextVariableError`

A subclass of `ConfigError`. Raised when a `Secret(...)` path references
a `{var}` that doesn't resolve against the root model's fields:

- At construction time (`validate="paths"` is default)
- At fetch time, if validation was deferred (standalone nested model)
  or skipped (`validate="none"`)

The exception message identifies the field and the missing variable.

### `SecretNotFoundError`

The backend confirmed the secret/key/path doesn't exist. Distinct from
`AuthError` (forbidden) — this is a 404 equivalent.

`RetryingBackend` does **not** retry this; the value isn't going to
appear by hammering the backend.

### `AuthError`

The backend rejected our credentials, token, or IAM identity. Distinct
from `SecretNotFoundError` — the path may exist, we just aren't allowed
to read it.

`RetryingBackend` does **not** retry this. `VaultBackend` with a
`token_factory=` configured *will* attempt a one-shot token renewal on
`Unauthorized` before raising.

### `TransientError`

A retryable backend failure: timeouts, throttling, 5xx responses, network
hiccups. `RetryingBackend` retries this with exponential backoff up to
`max_attempts` and `total_timeout`.

Backends use this category liberally — anything that isn't clearly
auth-or-not-found goes here so the retry layer can decide.

## What about my custom backend?

Map your SDK's exception types to the four leaf categories. As a guide:

| SDK category                      | Map to                  |
| --------------------------------- | ----------------------- |
| 404 / `KeyError` / "not found"    | `SecretNotFoundError`   |
| 401 / 403 / "permission denied"   | `AuthError`             |
| 5xx / timeouts / DNS / TCP resets | `TransientError`        |
| Anything ambiguous                | `TransientError` (let the retry layer decide) |

Never let a raw SDK exception leak out of `Backend.get`. Code in the
caller's hot path catches `VaultlyError` and expects nothing else.
