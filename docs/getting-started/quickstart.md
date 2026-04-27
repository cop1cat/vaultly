# Quickstart

Five minutes to a working config.

## 1. Declare your model

A `SecretModel` is a Pydantic `BaseModel` plus the `Secret(...)` field
declaration. Mix scalar fields and secrets freely:

```python
from vaultly import Secret, SecretModel


class AppConfig(SecretModel):
    stage: str = "dev"
    debug: bool = False
    db_password: str = Secret("/db/{stage}/password", ttl=300)
    api_key: str = Secret("/services/openai/key")
    max_conns: int = Secret("/db/{stage}/max_conns")
```

A few things to notice:

- `db_password: str` — that's the **type you'll work with**. `cfg.db_password`
  is a plain `str`, not a `SecretStr` proxy.
- `Secret("/db/{stage}/password")` — the path string can reference any
  *non-secret* field of the **root** model (`{stage}` here).
- `ttl=300` — cache the resolved value for 5 minutes. Default is "forever".
- `max_conns: int` — vaultly casts the raw backend string to `int` for you.

## 2. Pick a backend

For local development, env vars are easiest:

```python
from vaultly import EnvBackend
```

`EnvBackend` maps `/db/prod/password` → `DB_PROD_PASSWORD`. (See the [env
backend guide](../guides/choosing-a-backend.md) for prefixing rules.)

For tests, use the in-memory `MockBackend`:

```python
from vaultly import MockBackend

backend = MockBackend(
    {
        "/db/prod/password": "s3cr3t",
        "/services/openai/key": "sk-abc",
        "/db/prod/max_conns": "20",
    }
)
```

For real cloud backends:

```python
from vaultly.backends.aws_ssm import AWSSSMBackend
from vaultly.backends.vault import VaultBackend

aws = AWSSSMBackend(region_name="eu-west-1")
vault = VaultBackend(url="https://vault.example.com", token=os.environ["VAULT_TOKEN"])
```

## 3. Construct the model

```python
config = AppConfig(stage="prod", debug=True, backend=backend)
```

Path validation runs at construction time. If `Secret("/{stage}/x")`
references a field name that doesn't exist on the model, you get a clear
`MissingContextVariableError` immediately — no surprise at first fetch.

## 4. Use it

```python
config.db_password   # "s3cr3t" — fetched from backend, cached for 300s
config.api_key       # "sk-abc"
config.max_conns     # 20 — cast to int
config.stage         # "prod" — non-secret, no backend call
```

Repeat reads are cache hits; subsequent calls don't touch the backend.

## 5. Mask in logs / dumps

```python
print(config)
# > AppConfig(stage='prod', debug=True, db_password='***', api_key='***',
#            max_conns='***')

config.model_dump()
# > {'stage': 'prod', 'debug': True, 'db_password': '***',
#    'api_key': '***', 'max_conns': '***'}

config.model_dump_json()
# > {"stage": "prod", ..., "db_password": "***", ...}
```

!!! warning "Direct attribute access does NOT mask"
    `print(config.db_password)` will print the actual value. Use
    `model_dump`-based serialization for log output. See the [security
    model guide](../guides/security-model.md) for the full picture.

## 6. Refresh after rotation

```python
# An operator rotates the password externally.
config.refresh("db_password")    # invalidate + refetch this one field
config.refresh_all()             # invalidate the whole cache
```

## 7. Optional: prefetch at startup

Pin failures to startup so you don't discover a misconfigured secret three
hours into a deploy:

```python
class AppConfig(SecretModel, validate="fetch"):
    ...

# Construction now blocks until every secret is fetched. Any backend
# error surfaces immediately.
config = AppConfig(stage="prod", backend=backend)
```

## Next

- [SecretModel concepts](../concepts/secret-model.md) — the full lifecycle
  from declaration to fetch.
- [Path interpolation](../concepts/path-interpolation.md) — what `{var}`
  resolves against, including in nested models.
- [Choosing a backend](../guides/choosing-a-backend.md) — when to use which.
