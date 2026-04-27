# Caching

Every `SecretModel` has a thread-safe TTL cache. vaultly's whole point is
that you don't think about it — but when you do think about it, here's how
it works.

## One cache per root

Each root `SecretModel` instance owns one `TTLCache`. Nested children
share their root's cache (and its backend). Two unrelated roots have
independent caches; rotating one doesn't invalidate the other.

```python
prod = App(stage="prod", backend=b)
dev  = App(stage="dev",  backend=b)

prod.refresh_all()  # only clears prod's cache; dev untouched
```

## Keys

The cache key is the **resolved path** (with `{var}` filled in), optionally
suffixed with `@version` if the secret is pinned:

```text
/prod/db/password         # an unversioned secret
/prod/db/password@2       # version=2 of the same secret
```

Two distinct fields that produce the same key share one cache slot — and
one backend fetch.

## TTL semantics

Set per-field via `ttl=` on `Secret(...)`:

| Value         | Behavior                                                |
| ------------- | ------------------------------------------------------- |
| `None` (default) | The entry never expires.                             |
| `0`           | The entry is immediately stale; every read calls the backend. |
| `> 0`         | The entry lives `ttl` seconds.                          |

Expired entries are not deleted on read — they linger until overwritten
by a new fetch or until `invalidate` / `clear`. This is what
`stale_on_error` reads.

## The `prefetch()` flow

`prefetch()` (also triggered by `validate="fetch"`) walks the entire model
tree, splits secrets into versioned vs unversioned, then:

1. **Unversioned**: one `backend.get_batch(unique_paths)` call. Per-key
   locks are acquired *before* the batch call so a concurrent reader
   can't slip in and double-fetch.
2. **Versioned**: serial `backend.get(path, version=...)` calls (no batch
   API supports per-path versions).

Each value is cast to its field's annotated type and stored under its
cache key. Idempotent — calling `prefetch()` twice when the cache is
already warm is cheap (it still touches the backend; for true "warm
cache only" use the lazy default).

## Concurrency model

vaultly serializes only what it has to:

- **Hot reads** (cache hit) take only the cache's internal lock — no
  per-key lock. They scale across threads.
- **Cold reads** (cache miss for a given key) take that key's per-key
  lock, then double-check the cache, then fetch. Multiple threads racing
  on the *same* cold key see exactly one backend call.
- Threads racing on *different* cold keys don't block each other.
- `refresh(name)` holds the per-key lock across `invalidate + fetch`.
  A concurrent reader either sees the old value (briefly) or waits for
  the new fetch.

## Invalidation

| API                    | Effect                                           |
| ---------------------- | ------------------------------------------------ |
| `model.refresh(name)`  | Drop one secret from the cache and re-fetch it.  |
| `model.refresh_all()`  | Drop the entire cache.                           |
| (TTL expiry)           | Read raises a "miss" internally; backend fetched.|

Note that `refresh_all()` clears the **entire cache**, including
fields with `ttl=None`. After a rotation event, this is usually the
right hammer.

## Cache and `_fetch_locks` lifetime

`_fetch_locks` is a `KeyedLocks` (one `RLock` per resolved path). Locks
accumulate as new keys are fetched. For most apps the path set is bounded
by the model's shape and this is fine.

Multi-tenant apps that key by `tenant_id` (and so see thousands of unique
paths over time) can call `model._fetch_locks.discard(key)` or
`model._fetch_locks.clear()` at appropriate boundaries to reclaim
memory. Same for the `_cache` if entries are no longer needed:
`model._cache.clear()`.
