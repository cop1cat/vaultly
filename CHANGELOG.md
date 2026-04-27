# Changelog

All notable changes to this project will be documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0]
<!-- Set the date when cutting the tag. Format: YYYY-MM-DD -->

First public release.

### Highlights

- Mix regular Pydantic fields with secret-backed fields in one model.
  Secrets fetch lazily on first access, cache with a per-field TTL, and
  mask in `repr` / `model_dump` / JSON output.
- A secret-typed field is the actual type you declared (`str`, `int`,
  `dict`, …) — not a `SecretStr`-style proxy. Drivers and HTTP clients
  use the value directly, no adapters needed.
- Path templates (`Secret("/{stage}/db/password")`) interpolate from the
  root model's non-secret fields. Validation at construction catches
  typos before they become 3-a.m. paging.
- Anything that would otherwise leak the in-memory cache is blocked
  outright: `pickle.dumps`, `copy.copy`, `copy.deepcopy`, and
  `model_copy()` all raise `NotImplementedError`.

### `SecretModel`

- `Secret(path, *, ttl, transform, version, description)` field marker.
- `prefetch()` for eager loading; `refresh(name)` and `refresh_all()`
  for cache invalidation after rotation.
- Validation modes set via class kwargs:
  `class App(SecretModel, validate="fetch", stale_on_error=True)`.
- Nested `SecretModel` children share the root's backend, cache, and
  path-interpolation context.
- Thread-safe TTL cache, per-key reentrant fetch locks (no thundering
  herd on cold cache).
- Validation runs on every construction path — both `Foo(...)` and
  `Foo.model_validate({...})` / `model_validate_json(...)`.

### Backends

- `EnvBackend` — environment variables with optional prefix.
- `MockBackend` — in-memory dict with `(path, version)` call tracking
  for tests.
- `RetryingBackend` — wraps any backend; retries with exponential
  backoff, full jitter, and a wall-clock `total_timeout` budget.
  Pluggable `is_retryable=`, `backoff=`, and `on_retry=` hooks for
  custom retry policy and metrics.
- `AWSSSMBackend` (`pip install 'vaultly[aws]'`) — boto3, batched via
  `GetParameters` with auto-chunking at SSM's 10-name limit. Ships a
  sensible default `botocore.Config` (adaptive retries, 2 s connect /
  5 s read).
- `VaultBackend` (`pip install 'vaultly[vault]'`) — hvac, KV v2,
  `path:key` syntax for in-entry fields. Optional `token_factory=` for
  short-lived auth, plus `reuse_connection=` / `idle_timeout=` for
  deployments where idle TCP connections get dropped by an LB.
- `Backend` ABC for custom backends.

### Errors

```
VaultlyError
├── ConfigError
│   └── MissingContextVariableError
├── SecretNotFoundError       # not retried
├── AuthError                 # not retried
└── TransientError            # retried by RetryingBackend
```

Each backend maps SDK exceptions into this hierarchy. Cast / `transform`
failures are wrapped as `ConfigError` so callers' `except VaultlyError`
catches them; the original is preserved as `__cause__`.

### Tooling

- `py.typed` marker so downstream `mypy` / `pyright` pick up the
  annotations.
- Documentation site (mkdocs-material), versioned per release tag via
  `mike` with a `latest` alias.

### Known limitations

- `model_construct()` skips Pydantic's full validation by design — the
  path checks and prefetch don't run. Errors surface lazily on the
  first fetch.
- A standalone `SecretModel` whose `{var}` placeholders reference fields
  it doesn't own defers path validation. The model may later be wired
  into a parent that supplies the missing context. If never wired, the
  first fetch raises `MissingContextVariableError`.
- `Backend.get(path, *, version=None)` is the most likely candidate to
  evolve before 1.0 (a `SecretQuery`-shaped argument is on the table).
- No native async API yet. Bridge from coroutines via
  `asyncio.to_thread` until v0.2.

[Unreleased]: https://github.com/cop1cat/vaultly/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cop1cat/vaultly/releases/tag/v0.1.0
