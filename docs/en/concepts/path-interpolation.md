# Path interpolation

Most apps run multiple stages (dev / staging / prod), tenants, or regions,
and want one config class to cover all of them. vaultly's path
interpolation makes this declarative:

```python
class App(SecretModel):
    stage: str
    db_password: str = Secret("/{stage}/db/password")


prod = App(stage="prod", backend=...)  # reads /prod/db/password
dev  = App(stage="dev",  backend=...)  # reads /dev/db/password
```

## How `{var}` resolves

Placeholders use [`str.format`-style syntax](https://docs.python.org/3/library/string.html#format-string-syntax).
At fetch time, vaultly fills them with the model's **non-secret scalar fields**:

```python
class App(SecretModel):
    stage: str          # used for {stage}
    region: str         # used for {region}
    db_password: str = Secret("/{region}/{stage}/db/password")
```

When constructing, vaultly walks every secret's path, extracts placeholder
names, and verifies each one matches a non-secret field on the **root**
model. A typo raises `MissingContextVariableError` immediately:

```python
class Broken(SecretModel):
    stage: str
    db: str = Secret("/{stge}/db/password")   # {stge} typo


Broken(stage="prod", backend=...)
# > MissingContextVariableError: secret field Broken.db references {stge},
#   but no such field exists on the root model
```

## Nested models share the root context

Nested `SecretModel` fields don't get their own context — they always
resolve against the **root**. This is by design: it makes path templates
predictable and avoids ambiguity when the same `{var}` could come from
multiple levels.

```python
class DbConfig(SecretModel):
    password: str = Secret("/{stage}/db/password")
    pool_size: int = Secret("/{stage}/db/pool_size")
    # {stage} resolves against the *parent's* `stage` field

class App(SecretModel):
    stage: str
    db: DbConfig
```

A `DbConfig` constructed standalone (without a parent) defers path
validation — the model may be wrapped into a parent later. A standalone
DbConfig that's never wrapped surfaces the unresolved `{var}` as
`MissingContextVariableError` on the first fetch attempt.

## Allowed placeholders

| Form              | Supported?  | Notes                                         |
| ----------------- | ----------- | --------------------------------------------- |
| `{name}`          | yes         | The standard case.                            |
| `{{literal}}`     | yes         | Escaped braces — passes through as `{literal}`. |
| `{0}` (positional)| no          | Surfaces as `MissingContextVariableError`.    |
| `{x.attr}`        | no          | Same — we don't follow attribute paths.       |
| `{x[0]}`          | no          | Same — we don't follow indexing.              |

The four edge cases above are caught at fetch time with a clear
`MissingContextVariableError`, never as a generic `KeyError` /
`AttributeError`.

## When the value contains placeholders

The result of `path.format(**context)` is the resolved path used as the
cache key. If two distinct fields produce the same resolved path, they
share a single cache entry — and a single backend fetch.

This is sometimes useful (one secret used twice, one backend call) and
sometimes surprising (`Secret(...)` and `Secret(...)` declared twice for
the same path don't double-fetch). When in doubt, give each field a
distinct path or rely on the [Secret reference](secret-marker.md) for the
exact behavior.
