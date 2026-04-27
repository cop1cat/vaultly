# Changelog

All notable changes to this project will be documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0]
<!-- Set the date when cutting the tag. Format: YYYY-MM-DD -->


First public release.

### Added
- Core `SecretModel` with lazy fetch, repr / `model_dump` / JSON masking,
  path interpolation from non-secret fields, nested model trees,
  `prefetch()` / `refresh(name)` / `refresh_all()`.
- `Secret(...)` field marker with `path`, `ttl`, `transform`, `version`,
  `description` parameters.
- Built-in backends: `EnvBackend` (env vars with optional prefix),
  `MockBackend` (in-memory, with `(path, version)` call tracking),
  `RetryingBackend` (wraps any backend; retries `TransientError` with
  exponential backoff, jitter, and a hard wall-clock `total_timeout`).
- Optional cloud backends: `AWSSSMBackend` (boto3, batched via
  `GetParameters` with auto-chunking at the 10-name SSM limit) and
  `VaultBackend` (hvac, KV v2, `path:key` syntax for in-entry fields,
  optional `token_factory` for short-lived auth).
- `Backend` ABC for custom backends.
- Error hierarchy: `VaultlyError` → `ConfigError` /
  `MissingContextVariableError`, `SecretNotFoundError`, `AuthError`,
  `TransientError`.
- Subclass-level config via class kwargs:
  `class App(SecretModel, validate="fetch", stale_on_error=True)`.
  `_vaultly_*` ClassVars are also accepted.
- `model_validator(mode='after')` ensures path validation and prefetch
  run on `model_validate` / `model_validate_json` too — not only on
  `__init__`.
- Thread-safe TTL cache and per-key reentrant fetch locks
  (`KeyedLocks` with `discard` / `clear` for multi-tenant cache hygiene).
- `py.typed` marker so downstream type checkers pick up annotations
  without `mypy --no-strict-optional` noise.
- Docs site (mkdocs-material) covering concepts, guides, and an
  auto-generated API reference; deployed via `mike` per release tag with
  a `latest` alias.

### Changed
- `Backend.get_batch` deduplicates input paths before fanning out — SSM
  in particular rejects duplicate names within a single
  `GetParameters` request.
- `AWSSSMBackend` ships a default `botocore.Config` (`mode=adaptive`,
  `max_attempts=3`, `connect_timeout=2s`, `read_timeout=5s`) when no
  explicit `config=` is given. Override via `config=` kwarg.
- `VaultBackend.get` normalizes non-string KV values via `json.dumps`,
  so a Vault entry holding a dict / list / int / bool round-trips
  correctly through vaultly's cast layer.
- `cast_value` unwraps `Optional[T]` / `T | None` annotations to the
  underlying `T` before casting (otherwise an `int | None` field would
  silently get the raw string back).
- Cast / `transform=` exceptions are wrapped as `ConfigError` so callers'
  `except VaultlyError` catches them. The original is preserved as
  `__cause__`.
- Vaultly's logger now ships with a `NullHandler` attached so
  unconfigured apps don't see warnings on stderr.

### Disabled
- `SecretModel.model_copy()`, `copy.copy(model)`, `copy.deepcopy(model)`,
  and `pickle.dumps(model)` all raise `NotImplementedError`. Each would
  either share or duplicate the in-memory cleartext cache and break
  nested `_root` linkage. Construct a fresh instance instead.

### Known limitations
- `model_construct()` skips Pydantic's full validation by design —
  vaultly's path validation and prefetch don't run. Errors surface
  lazily at first fetch.
- Standalone construction of a `SecretModel` whose `{var}` placeholders
  reference fields it doesn't own defers path validation. The model may
  later be wired into a parent that provides the missing context, in
  which case the parent's validation pass covers it. If never wired,
  the first fetch surfaces a clean `MissingContextVariableError`.
- `Backend.get(path, *, version=None)` signature is the most likely
  candidate to evolve before 1.0 (see [Breaking-change policy](https://dspiridonov.github.io/vaultly/guides/security-model/)).
- Async / `AsyncBackend` is not yet supported. Use `asyncio.to_thread`
  to bridge from coroutines until v0.2.

[Unreleased]: https://github.com/dspiridonov/vaultly/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dspiridonov/vaultly/releases/tag/v0.1.0


