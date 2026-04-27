# vaultly

**Declarative, Pydantic-native secrets manager for Python 3.12+.**

Mix regular Pydantic fields with secret-backed fields in one model. Secrets
are fetched lazily on first access, cached with per-field TTL, masked in
`repr` and `model_dump`, and never carry a different type than the one you
declared.

```python
from vaultly import Secret, SecretModel
from vaultly.backends.aws_ssm import AWSSSMBackend


class AppConfig(SecretModel):
    stage: str = "dev"
    debug: bool = False
    db_password: str = Secret("/db/{stage}/password", ttl=300)
    api_key: str = Secret("/services/openai/key")
    max_conns: int = Secret("/db/{stage}/max_conns")


config = AppConfig(stage="prod", backend=AWSSSMBackend(region_name="eu-west-1"))

config.db_password   # -> str, fetched on first access, cached for 300s
config.max_conns     # -> int, cast from "42"
config.model_dump()  # -> {..., "db_password": "***", "api_key": "***"}
```

## Why

Secret loading in most apps is a tangle of `os.getenv`, vendor-specific
clients, ad-hoc caching, and `# TODO: rotate me` comments. vaultly compresses
that to a single Pydantic model that:

- Works with the type system you already use — `cfg.db_password` is a plain
  `str`, `cfg.max_conns` is a real `int`.
- Downstream libraries (psycopg, httpx, Redis clients) need no adapters.
- Doesn't leak in output — `repr`, `str`, `model_dump`, and JSON output mask
  every secret field.
- Refuses to be cloned — `copy.copy`, `copy.deepcopy`, `model_copy`, and
  `pickle` won't operate on a model that holds cached secrets.
- Is explicit about retries and TTL — no surprise behavior on a 5xx storm,
  no surprise behavior at midnight when a TTL expires.
- Is testable — the in-memory `MockBackend` plugs in identically to real
  ones, with call tracking for assertions.

## Status

Pre-1.0. The public surface is stable for the documented backends; some
internals (notably `Backend.get` signature) may evolve before 1.0. See the
[changelog](https://github.com/cop1cat/vaultly/blob/main/CHANGELOG.md)
and the breaking-change note at the end of this site.

## Where to start

- New here? → [Quickstart](getting-started/quickstart.md)
- Switching from `pydantic-settings`? → [SecretModel concepts](concepts/secret-model.md)
- Picking a backend? → [Choosing a backend](guides/choosing-a-backend.md)
- Operating in prod? → [Security model](guides/security-model.md) and
  [Concurrency](guides/concurrency.md)
