# Ретраи и stale-on-error

vaultly обрабатывает transient-сбои бэкендов двумя opt-in механизмами:

- **`RetryingBackend`** — оборачивает бэкенд и ретраит `TransientError`
  с экспоненциальным backoff'ом.
- **`stale_on_error`** — model-level fallback к последнему кэшированному
  значению, когда transient-сбой исчерпал retry-бюджет.

Эти механизмы композиционны. Типичный production-стек использует оба.

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

Поведение:

- Ретраит **только** `TransientError`. `SecretNotFoundError` и `AuthError`
  всплывают сразу — они сами не починятся.
- Backoff экспоненциальный с full jitter (равномерно `[0, computed_delay]`)
  по умолчанию. `jitter=False` — детерминированное время для тестов.
- `total_timeout` — жёсткий wall-clock бюджет. После исчерпания vaultly
  останавливает ретраи, даже если `max_attempts` позволяло бы больше.
  По умолчанию 10с.
- Каждый ретрай логируется на WARNING с лейблом пути и computed delay.

### Зачем нужны ОБА — `max_attempts` и `total_timeout`?

`max_attempts` ограничивает количество запросов к бэкенду;
`total_timeout` ограничивает общее wall-time, включая sleep'ы. Срабатывает
тот, что короче.

Для бэкенда с 5с read timeout, `max_attempts=5` могут потратить 25с+
только на чтения; `total_timeout=10` держит worst-case ограниченным
независимо.

## stale_on_error

```python
class App(SecretModel, stale_on_error=True):
    db_password: str = Secret("/db/password", ttl=60)
```

Когда outage исчерпывает retry-бюджет, vaultly ищет *просроченное*
значение в кэше для этого пути. Если оно есть — пишет warning в лог и
возвращает stale-значение. Если ничего никогда не было закэшировано —
оригинальный `TransientError` пробрасывается как обычно.

Используйте в read-mostly нагрузках, где обслуживание слегка устаревших
credentials во время outage предпочтительнее краха. **Не** используйте
для credentials, предназначенных для горячей ротации (например короткие
AWS STS-токены) — stale-значение будет отвергнуто downstream-сервисом
и вы сожжёте error budget там.

## Как слои композиционируют

```text
ваш код
  ↓
SecretModel._fetch
  ↓                                   (ретраи внутри)
RetryingBackend.get  ← attempts × max_attempts, capped by total_timeout
  ↓
AWSSSMBackend.get
  ↓
boto3 SSM client     ← уже имеет свои transport-уровневые ретраи
```

Задранный retry-budget boto3 И задранный retry-budget RetryingBackend
дают умножение outage-времени. Ориентир:

- Пусть boto3 / hvac разбираются с transport-уровнем (DNS, TCP, 5xx с
  коротким backoff'ом). Используйте дефолты SDK — vaultly уже
  настраивает консервативные для `AWSSSMBackend`.
- Используйте `RetryingBackend` для прикладной retry-логики, где нужна
  видимость (он логирует каждый ретрай) и жёсткий total-timeout.

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

1. `validate="fetch"` вызывает `prefetch()`. vaultly делает один batched
   `GetParameters`-вызов на всё.
2. Если SSM 5xx-ит, `RetryingBackend` ретраит до 3 раз с backoff'ом,
   ограниченным 8с.
3. Если всё ещё фейлит — старт поднимает `TransientError`. Дальше не идём.

Что происходит на 6-й минуте, когда истекает TTL `db_password`:

1. Читатель вызывает `config.db_password`.
2. Cache miss; пробуем фетч из бэкенда.
3. Идёт SSM 5xx-шторм.
4. `RetryingBackend` ретраит; сдаётся после 3 попыток / 8с бюджета.
5. `stale_on_error=True` срабатывает → возвращает прошлое значение с
   WARNING-логом в `vaultly` логгер.
6. Сервис не упал. Оператор получит paging из лога.
