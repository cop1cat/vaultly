# Ретраи и stale-on-error

vaultly даёт два механизма для борьбы с временными сбоями бэкенда —
оба opt-in:

- **`RetryingBackend`** — обёртка, ретраящая `TransientError` с
  экспоненциальным backoff'ом.
- **`stale_on_error`** — на уровне модели: если retry-бюджет
  исчерпан, вернуть последнее закэшированное значение, даже если оно
  просрочено.

В типичном prod-стеке используют оба.

## RetryingBackend: дефолтное поведение

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

- Ретраит **только** `TransientError`. `SecretNotFoundError` и
  `AuthError` всплывают сразу — они сами не починятся.
- Backoff экспоненциальный, с full jitter (равномерно
  `[0, computed_delay]`). Для предсказуемого тайминга в тестах —
  `jitter=False`.
- `total_timeout` — жёсткий бюджет по wall-clock'у. После исчерпания
  vaultly останавливает ретраи, даже если `max_attempts` ещё
  позволял бы. По умолчанию 10 секунд.
- Каждый ретрай логируется на WARNING с лейблом пути и вычисленной
  задержкой.

### Зачем нужны и `max_attempts`, и `total_timeout`

`max_attempts` ограничивает количество запросов. `total_timeout`
ограничивает суммарное время с учётом sleep'ов. Срабатывает тот, что
короче.

Для бэкенда с 5-секундным read timeout, `max_attempts=5` могут потратить
25+ секунд только на чтения; `total_timeout=10` держит worst-case в
рамках, что бы ни происходило.

## RetryingBackend: своя логика

Когда дефолт не подходит, есть три callback'а.

### `is_retryable` — что считать ретраеспособным

```python
from vaultly import SecretNotFoundError, TransientError

def my_predicate(exc: BaseException) -> bool:
    # Eventually-consistent бэкенд: только что записанный секрет
    # ещё не виден. Дайте retry-слою попробовать ещё раз.
    return isinstance(exc, (TransientError, SecretNotFoundError))


backend = RetryingBackend(inner, is_retryable=my_predicate)
```

По умолчанию ретраится только `TransientError`. Своим предикатом можно
расширить (как выше) или сузить (например, не ретраить ничего, чтобы
все ошибки всплывали сразу).

### `backoff` — своя формула задержки

```python
# Фиксированная задержка между попытками.
backend = RetryingBackend(inner, backoff=lambda _attempt: 1.0)

# Decorrelated jitter, как в AWS Architecture Blog.
import random
def decorrelated(attempt: int) -> float:
    prev = getattr(decorrelated, "_prev", 0.5)
    nxt = min(20.0, random.uniform(0.5, prev * 3))
    decorrelated._prev = nxt
    return nxt

backend = RetryingBackend(inner, backoff=decorrelated)
```

Когда задан `backoff=`, дефолтная формула с `base_delay` / `max_delay` /
`jitter` не используется.

### `on_retry` — callback для метрик и breadcrumbs

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

Callback вызывается **перед** каждым sleep'ом. Если он сам поднимет
исключение — vaultly его залогирует и продолжит ретраи (callback
обязан быть «дешёвым» и не критичным).

## stale_on_error

```python
class App(SecretModel, stale_on_error=True):
    db_password: str = Secret("/db/password", ttl=60)
```

Когда outage исчерпывает retry-бюджет, vaultly смотрит в кэш — есть ли
там просроченное значение для этого пути. Если есть — пишет warning в
лог и возвращает его. Если ничего никогда не кэшировалось,
`TransientError` всплывает как обычно.

Используйте на read-mostly нагрузках, где отдать слегка устаревшие
учётные данные во время outage'а лучше, чем упасть. **Не используйте**
для секретов, специально предназначенных для горячей ротации (короткие
AWS STS-токены) — устаревшее значение всё равно отвергнет downstream,
и вы только сожжёте error budget там.

## Как слои композируются

```text
ваш код
  ↓
SecretModel._fetch
  ↓                                   (ретраи внутри)
RetryingBackend.get  ← attempts × max_attempts, capped by total_timeout
  ↓
AWSSSMBackend.get
  ↓
boto3 SSM client     ← у него свои transport-уровневые ретраи
```

Если задрать в high оба бюджета (boto3 и `RetryingBackend`) — outage
умножается. Ориентир:

- Transport-уровень (DNS, TCP, 5xx с коротким backoff'ом) пусть
  обрабатывают boto3 / hvac. Используйте дефолты SDK — vaultly уже
  настраивает консервативные для `AWSSSMBackend`.
- `RetryingBackend` — для прикладной retry-логики, где нужна
  видимость (логи, callback) и жёсткий total-timeout.

## Рецепт: rotate-resilient prod-стек

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

Что происходит при старте:

1. `validate="fetch"` вызывает `prefetch()`. vaultly делает один
   batched `GetParameters`-вызов на всё.
2. Если SSM 5xx-ит, `RetryingBackend` ретраит до 3 раз с backoff'ом,
   ограниченным 8 секундами.
3. Если всё ещё фейл — старт поднимает `TransientError`. Дальше не идём.

Что происходит на 6-й минуте, когда истекает TTL `db_password`:

1. Читатель вызывает `config.db_password`.
2. Cache miss; пытаемся фетчить.
3. Идёт SSM 5xx-шторм.
4. `RetryingBackend` ретраит, сдаётся после 3 попыток или 8 секунд.
5. Срабатывает `stale_on_error=True` → возвращаем прошлое значение
   с WARNING в `vaultly` логгер.
6. Сервис не падает. Оператор видит warning через свой logging stack.
