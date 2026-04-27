# Concurrency

vaultly is built for the typical Python service shape: many threads
sharing one config instance, possibly inside an async event loop, possibly
under load.

## Threading

A `SecretModel` is safe to share across threads. Reads and writes to the
internal cache are protected by a `threading.Lock`; cold-cache fetches
are protected by per-key `threading.RLock` so 100 threads asking for the
same secret simultaneously produce **exactly one** backend call.

```python
config = AppConfig(stage="prod", backend=...)

# Safe from any number of threads.
db_pw = config.db_password
api_k = config.api_key
```

What's protected:

- `cache.get` / `cache.set` / `cache.invalidate` / `cache.peek_expired`
- The cold-cache fetch sequence (lock per resolved cache key)
- `refresh(name)` (holds the per-key lock across `invalidate + fetch`)
- `prefetch()` (acquires every per-key lock before the batch call)

What's **not** protected:

- Mutation of your model's own non-secret fields (e.g. `config.stage = "dev"`)
  — Pydantic doesn't lock these; you'd need `model_config =
  ConfigDict(frozen=True)` for true immutability or to do this only at
  startup.
- Replacing the `backend` field (`config.backend = new_backend`) —
  semantically unsupported; reload by reconstructing the model.

## Hot-path performance

Cache **hits** take only the lightweight cache lock, not the per-key
lock. They scale across threads. The integration test
`test_warm_cache_hot_reads_dont_serialize` confirms 200,000 reads across
20 threads complete well under a second.

A regression that ever serializes hot reads — say, by always taking the
per-key lock — would balloon that number 100×.

## Asyncio

vaultly is **synchronous**. There's no `aget`, no `AsyncBackend`. Calls
to `config.db_password` block the event loop while the backend round-trip
runs.

For typical configs that load at startup (`validate="fetch"`) and serve
from cache thereafter, this is fine — the only blocking call happens
during boot.

For configs that fetch lazily during request handling and you can't
afford to block, wrap in `asyncio.to_thread`:

```python
import asyncio

# Inside an async handler:
db_pw = await asyncio.to_thread(lambda: config.db_password)
```

A native async API is on the v0.2 roadmap.

## Process boundaries

vaultly doesn't share state across processes. Each process is its own
cache:

- `multiprocessing` — workers each construct their own model; each has
  its own backend + cache.
- `gunicorn` (forking) — child processes inherit the model from the
  parent; the cache *is* shared via fork-copy, but only as a snapshot.
  Mutations by one child don't propagate. Construct in each child if you
  rely on per-child rotation.
- `gevent` / `eventlet` — vaultly's `threading.Lock` becomes a green-lock
  under monkey-patching. Should work but is not specifically tested.

## Fork safety

vaultly doesn't currently install `os.register_at_fork` handlers. If you
fork after touching the cache, the child inherits a snapshot of the
in-memory state including the locks. Best practice: construct your
`SecretModel` *after* forking (in each worker), not before.

## Per-tenant patterns

If your app multi-tenants by interpolating tenant id into the path:

```python
class TenantConfig(SecretModel):
    tenant_id: str
    api_key: str = Secret("/tenants/{tenant_id}/api_key", ttl=300)
```

You probably want one `TenantConfig` instance per tenant, each with its
own cache. The default cache + lock dicts grow as new resolved paths are
seen — bounded by `tenant_count × secrets_per_tenant`.

For very high tenant counts, periodically reclaim:

```python
# Drop a specific entry.
config._cache.invalidate(resolved_path)
config._fetch_locks.discard(resolved_path)

# Or nuke everything (e.g. at the end of a request).
config._cache.clear()
config._fetch_locks.clear()
```

These are private APIs but stable for this use case.
