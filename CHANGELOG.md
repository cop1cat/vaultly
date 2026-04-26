# Changelog

All notable changes to this project will be documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Core `SecretModel` with lazy fetch, masking, path interpolation, nested
  trees, `prefetch()` / `refresh()` / `refresh_all()`.
- `Secret(...)` marker accepting `path`, `ttl`, `transform`, `version`,
  `description`.
- `Backend` ABC and built-in backends: `EnvBackend`, `MockBackend`,
  `AWSSSMBackend` (`[aws]` extra, batched), `VaultBackend` (`[vault]` extra,
  KV v2, `path:key` syntax, `token_factory` for renewal).
- `RetryingBackend` wrapper with exponential backoff, jitter, and a
  wall-clock `total_timeout` budget.
- Validation modes via class kwargs: `class App(SecretModel, validate="fetch",
  stale_on_error=True)`. `_vaultly_validate` / `_vaultly_stale_on_error`
  ClassVars are still honored as a fallback.
- Per-key fetch lock to prevent thundering herd on cold cache.
- `model_validator(mode='after')` so path validation and prefetch run on the
  `model_validate` / `model_validate_json` paths too, not only `__init__`.
- `py.typed` marker so downstream type checkers pick up our annotations.

### Changed
- `Backend.get_batch` now deduplicates inputs before fanning out.
- `AWSSSMBackend` ships a default `botocore.Config` (adaptive retries,
  2s connect / 5s read timeouts) when no explicit `config=` is given.

### Disabled
- `SecretModel.model_copy()` raises `NotImplementedError` — copying would
  duplicate the cache and break nested-root linkage. Construct a fresh
  instance instead.

### Known limitations
- `model_construct()` skips validation by Pydantic design; vaultly's
  path-validation and prefetch don't run. Errors surface at first fetch.
- Standalone construction of a model whose `{var}`s aren't all in its own
  fields defers validation until the model is wired into a parent or
  fetched. Typos and "intend-to-be-nested" both look the same at this point.
