# Retries and stale-on-error

vaultly handles transient backend failures via two opt-in mechanisms:

- **`RetryingBackend`** — wraps a backend and retries `TransientError`
  with exponential backoff.
- **`stale_on_error`** — model-level fallback to the last cached value
  when a transient failure exhausts the retry budget.

These compose. The typical production stack uses both.

## RetryingBackend

```python
from vaultly import RetryingBackend
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = RetryingBackend(
    AWSSSMBackend(region_name="eu-west-1"),
    max_attempts=3,
    base_delay=0.5,
    max_delay=4.0,
    total_timeout=10.0,
    jitter=True,
)
```

Behavior:

- Retries **only** `TransientError`. `SecretNotFoundError` and `AuthError`
  surface immediately — they're not going to fix themselves.
- Backoff is exponential with full jitter (uniform `[0, computed_delay]`)
  by default. Set `jitter=False` for deterministic timing in tests.
- `total_timeout` is a hard wall-clock budget. Once exceeded, vaultly
  stops retrying even if `max_attempts` would allow more. Default 10s.
- Logs each retry at WARNING with the path label and computed delay.

### Why both `max_attempts` and `total_timeout`?

`max_attempts` caps how many times you ask the backend; `total_timeout`
caps total wall time including sleeps. The shorter one wins.

For a backend with 5s read timeout, `max_attempts=5` could spend 25s+
just on reads; `total_timeout=10` keeps the worst case bounded regardless.

## Custom retry logic

When the defaults don't fit, three callbacks let you tune behavior.

### `is_retryable` — what counts as retryable

```python
from vaultly import SecretNotFoundError, TransientError

def my_predicate(exc: BaseException) -> bool:
    # Eventually-consistent backend: a just-written secret may not be
    # visible yet. Let the retry layer try again.
    return isinstance(exc, (TransientError, SecretNotFoundError))


backend = RetryingBackend(inner, is_retryable=my_predicate)
```

By default, only `TransientError` is retried. A custom predicate can
broaden the set (as above) or narrow it — e.g. retry nothing so every
error surfaces immediately.

### `backoff` — your own delay formula

```python
# Fixed delay between attempts.
backend = RetryingBackend(inner, backoff=lambda _attempt: 1.0)

# Decorrelated jitter, AWS Architecture Blog style.
import random
def decorrelated(attempt: int) -> float:
    prev = getattr(decorrelated, "_prev", 0.5)
    nxt = min(20.0, random.uniform(0.5, prev * 3))
    decorrelated._prev = nxt
    return nxt

backend = RetryingBackend(inner, backoff=decorrelated)
```

When `backoff=` is set, the default `base_delay` / `max_delay` /
`jitter` formula is bypassed.

### `on_retry` — hook for metrics and breadcrumbs

```python
from prometheus_client import Counter
RETRIES = Counter("vaultly_retries_total", "...", ["path"])

def hook(attempt, exc, delay):
    RETRIES.labels(path=str(exc)).inc()
    sentry_sdk.add_breadcrumb(
        category="vaultly", message=f"retry {attempt}: {exc}",
    )

backend = RetryingBackend(inner, on_retry=hook)
```

The callback fires **before** each sleep. If it raises, vaultly logs
the exception and continues retrying — the hook must be cheap and
non-critical.

## stale_on_error

```python
class App(SecretModel, stale_on_error=True):
    db_password: str = Secret("/db/password", ttl=60)
```

When an outage exhausts the retry budget, vaultly looks for an *expired*
cached value for that path. If one exists, it logs a warning and returns
the stale value. If nothing was ever cached, the original `TransientError`
propagates as usual.

Use this for read-mostly workloads where serving slightly stale
credentials during a backend outage is preferable to crashing. **Don't**
use this for credentials that are meant to be hot-rotated (e.g. AWS STS
short-lived tokens) — a stale value will be rejected by the downstream
service and you'll waste error budget there instead.

## How the layers compose

```text
your code
  ↓
SecretModel._fetch
  ↓                                   (retries inside)
RetryingBackend.get  ← attempts × max_attempts, capped by total_timeout
  ↓
AWSSSMBackend.get
  ↓
boto3 SSM client     ← already has its own transport-level retries
```

Setting boto3's retry budget high AND `RetryingBackend` retry budget high
multiplies the outage time. As a rule of thumb:

- Let boto3 / hvac handle transport-level (DNS, TCP, 5xx with
  short backoff). Use the SDK defaults — vaultly already configures
  conservative ones for `AWSSSMBackend`.
- Use `RetryingBackend` for application-level retry logic where you want
  visibility (it logs each retry) and a hard total-timeout budget.

## Recipe: rotate-resilient prod stack

```python
from vaultly import RetryingBackend, Secret, SecretModel
from vaultly.backends.aws_ssm import AWSSSMBackend


class App(SecretModel, validate="fetch", stale_on_error=True):
    stage: str
    db_password: str = Secret("/{stage}/db/password", ttl=300)
    api_key: str = Secret("/services/openai/key", ttl=900)


backend = RetryingBackend(
    AWSSSMBackend(region_name="eu-west-1"),
    max_attempts=3,
    total_timeout=8.0,
)
config = App(stage="prod", backend=backend)
```

What happens at boot:

1. `validate="fetch"` calls `prefetch()`. vaultly issues one batched
   `GetParameters` call for everything.
2. If SSM 5xx's, `RetryingBackend` retries up to 3× with backoff, capped
   at 8s.
3. If still failing — startup raises `TransientError`. Don't continue.

What happens at minute 6, when `db_password`'s TTL is up:

1. Reader calls `config.db_password`.
2. Cache miss; backend fetch is attempted.
3. SSM 5xx storm in progress.
4. `RetryingBackend` retries; gives up after 3 attempts / 8s budget.
5. `stale_on_error=True` kicks in → return the previous value with a
   WARNING log entry to `vaultly` logger.
6. Service stays up. Operator gets paged from the log.
