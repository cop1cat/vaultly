# Выбор бэкенда

Дерево решений:

```text
┌─ unit / integration тест?
│  └─ MockBackend  (in-memory dict, ведёт журнал вызовов)
│
├─ локальная разработка / простой контейнерный деплой / CI?
│  └─ EnvBackend   (env vars, опциональный префикс)
│
├─ сервис на AWS, секреты в SSM Parameter Store?
│  └─ AWSSSMBackend  (как правило в обёртке RetryingBackend)
│
├─ инфра на Vault?
│  └─ VaultBackend (в обёртке RetryingBackend, с token_factory)
│
└─ что-то ещё (Azure KV, GCP SM, своё)?
   └─ Подкласс Backend, ~30 строк (см. концепцию Бэкендов)
```

## EnvBackend

Самый дешёвый вариант. Отображает `/db/prod/password` →
`DB_PROD_PASSWORD`.

```python
from vaultly import EnvBackend

backend = EnvBackend()                  # без префикса
backend = EnvBackend(prefix="MYAPP")    # MYAPP_DB_PROD_PASSWORD
backend = EnvBackend(prefix="MYAPP_")   # MYAPP_DB_PROD_PASSWORD (auto-de-dup)
```

Между префиксом и ключом автоматически вставляется одно подчёркивание,
если префикс не оканчивается на `_`.

**Не подходит** для production-секретов: env vars видны любому, у кого
есть доступ к `/proc/<pid>/environ`. Для серьёзных секретов используйте
выделенное хранилище.

## MockBackend

Для тестов. На вход — dict path → value. Ведёт журнал вызовов, чтобы
можно было проверять поведение кэша.

```python
from vaultly import MockBackend

b = MockBackend({"/db/password": "s3cr3t", "/api/key": "sk"})
config = AppConfig(stage="prod", backend=b)
config.db_password         # "s3cr3t"
b.calls                    # [("/db/password", None)]
```

Для версионированных секретов — отдельный `versions=`:

```python
b = MockBackend(versions={("/db/password", 2): "older"})
```

`MockBackend` поднимает `SecretNotFoundError` для отсутствующих ключей —
так же, как и реальные бэкенды. Тесты на error-path работают одинаково.

## AWSSSMBackend

```python
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = AWSSSMBackend(region_name="eu-west-1")
```

По умолчанию использует разумные production-таймауты (2с connect / 5с
read) и adaptive-ретраи. Для override передайте `config=`:

```python
from botocore.config import Config

backend = AWSSSMBackend(
    region_name="eu-west-1",
    config=Config(
        retries={"mode": "standard", "max_attempts": 5},
        connect_timeout=1.0,
        read_timeout=3.0,
    ),
)
```

См. [Гайд по AWS SSM](aws-ssm.md) для полной матрицы фич: SecureString,
batched чтение, версии.

## VaultBackend

```python
from vaultly.backends.vault import VaultBackend

backend = VaultBackend(
    url="https://vault.example.com",
    token=os.environ["VAULT_TOKEN"],
    mount_point="secret",        # KV v2 mount point (default)
    default_key="value",         # поле в dict секрета (default)
)
```

Для коротких токенов (AppRole, K8s auth, JWT) передайте `token_factory=`
— callable, возвращающий свежий токен. vaultly вызовет его на
`Unauthorized` и повторит чтение.

См. [Гайд по Vault](vault.md): синтаксис `path:key`, специфика KV v2,
паттерны обновления токенов, управление соединением.

## RetryingBackend

Оборачивает любой другой бэкенд. По умолчанию ретраит только
`TransientError` (таймауты, throttling, 5xx). Auth и not-found не
ретраятся.

```python
from vaultly import RetryingBackend
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = RetryingBackend(
    AWSSSMBackend(region_name="eu-west-1"),
    max_attempts=3,
    base_delay=0.5,
    max_delay=4.0,
    total_timeout=10.0,
)
```

`total_timeout` — жёсткий бюджет по wall-clock'у. Даже если
`max_attempts` позволял бы больше, vaultly остановит ретраи когда
бюджет исчерпан. Это не даёт 30-минутному outage'у превратиться в
30-минутный hang при старте.

Если нужна логика отличающаяся от дефолта — есть три callback'а:

- `is_retryable=` — что считать ретраеспособным.
- `backoff=` — своя формула задержки.
- `on_retry=` — callback на каждое событие; для метрик и breadcrumbs.

Подробности — в [Ретраи и stale-on-error](retries-and-stale.md).
