# Nested config trees

Real services don't have one flat config. They have a DB section, a
cache section, an API-keys section, etc. vaultly supports nesting
`SecretModel` instances naturally.

```python
from vaultly import Secret, SecretModel


class DbConfig(SecretModel):
    password: str = Secret("/{stage}/db/password")
    pool_size: int = Secret("/{stage}/db/pool_size")


class CacheConfig(SecretModel):
    redis_url: str = Secret("/{stage}/cache/url")


class App(SecretModel):
    stage: str
    db: DbConfig
    cache: CacheConfig
    openai_key: str = Secret("/services/openai/key")
```

## Construction

Construct nested children inline as dicts (Pydantic's standard pattern):

```python
config = App(stage="prod", db={}, cache={}, backend=AWSSSMBackend(...))
```

The dict goes through Pydantic's validator, which produces a `DbConfig` /
`CacheConfig` instance. vaultly's `model_validator(mode='after')` then
wires the children to the root.

You can also pre-construct the children:

```python
config = App(stage="prod", db=DbConfig(), cache=CacheConfig(), backend=...)
```

A child constructed this way without a parent context defers path
validation — `DbConfig()` standalone has no `{stage}` field, but vaultly
recognizes that and leaves the resolution to whoever wraps it.

## What nested children share with their root

A child wired into a root borrows three things from it:

- The **backend** — only the root's `backend=` is used; children's
  `backend` field is ignored after wiring.
- The **cache** — `config.db.refresh("password")` invalidates the same
  cache slot that `config.refresh(...)` would.
- The **path context** — `{stage}` in `DbConfig` resolves against the
  root's `stage` field, not against `DbConfig` itself.

## Refresh from anywhere in the tree

`refresh` and `refresh_all` walk the same shared cache regardless of
where on the tree you call them:

```python
# Equivalent: both invalidate /prod/db/password.
config.db.refresh("password")
config.db._effective_root().refresh_all()  # nukes everything
```

Most apps just call `config.refresh_all()` after a deployment / rotation
event.

## Path-validation across the tree

`validate="paths"` walks the whole tree at construction. A typo in any
nested field surfaces immediately, with the path to the offending field
in the error message:

```python
class BadDb(SecretModel):
    password: str = Secret("/{stge}/password")   # typo

class App(SecretModel):
    stage: str
    db: BadDb

App(stage="prod", db={}, backend=...)
# > MissingContextVariableError: secret field BadDb.password references {stge},
#   but no such field exists on the root model
```

## Limits

- **Cycles** between models aren't supported (and Pydantic would refuse
  to construct one anyway).
- **Lists / dicts of `SecretModel`** are not specifically supported —
  they're regular Pydantic field types from vaultly's perspective. Don't
  put a `list[SecretModel]` in a model and expect each element's secrets
  to share the parent's cache. Use a single `SecretModel` shape per
  logical instance.
- **Don't `model_copy` a child** to reuse it in another tree. Copying is
  blocked precisely because the cache and `_root` linkage would be
  ambiguous. Construct fresh.
