# Выбор бэкенда

Дерево решений:

```text
┌─ Это unit/integration-тест?
│  └─ MockBackend  (in-memory dict, трекает вызовы)
│
├─ Локальная разработка / простой контейнерный деплой / CI?
│  └─ EnvBackend   (env vars, опциональный prefix)
│
├─ Работа на AWS, секреты в SSM Parameter Store?
│  └─ AWSSSMBackend  (скорее всего обёрнутый в RetryingBackend)
│
├─ HashiCorp Vault для всего?
│  └─ VaultBackend (скорее всего обёрнутый в RetryingBackend, с token_factory)
│
└─ Что-то ещё (Azure KV, GCP SM, кастом)?
   └─ Подкласс Backend, ~30 строк (см. концепцию Backends)
```

## EnvBackend

Самый низкофрикционный вариант. Маппит `/db/prod/password` →
`DB_PROD_PASSWORD`.

```python
from vaultly import EnvBackend

backend = EnvBackend()                  # без префикса
backend = EnvBackend(prefix="MYAPP")    # MYAPP_DB_PROD_PASSWORD
backend = EnvBackend(prefix="MYAPP_")   # MYAPP_DB_PROD_PASSWORD (auto-de-dup)
```

Между префиксом и ключом автоматически вставляется одно подчёркивание,
если префикс не оканчивается на `_`.

**Не используйте** для production-grade секретов. Env vars видны любому,
у кого есть доступ к `/proc/<pid>/environ`; для реальных секретов
используйте выделенный secret store.

## MockBackend

Для тестов. Конструируется с dict path → value. Трекает каждый вызов,
чтобы можно было проверять поведение кэша.

```python
from vaultly import MockBackend

b = MockBackend({"/db/password": "s3cr3t", "/api/key": "sk"})
config = AppConfig(stage="prod", backend=b)
config.db_password         # "s3cr3t"
b.calls                    # [("/db/password", None)]
```

Для версионированных секретов передайте отдельный `versions=`:

```python
b = MockBackend(versions={("/db/password", 2): "older"})
```

`MockBackend` поднимает `SecretNotFoundError` для отсутствующих ключей,
совпадая с контрактом реальных бэкендов — error-path тесты работают
одинаково.

## AWSSSMBackend

```python
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = AWSSSMBackend(region_name="eu-west-1")
```

По умолчанию использует разумные production-таймауты (2с connect / 5с
read) и adaptive-ретраи. Передайте `config=` для переопределения:

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

См. [Гайд по AWS SSM](aws-ssm.md) для полной матрицы фич (SecureString,
batch, версии).

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

Для короткоживущих токенов (AppRole, K8s auth, JWT) передайте
`token_factory=` callable, возвращающий свежий токен. vaultly вызывает
его один раз на `Unauthorized` и повторяет чтение.

См. [Гайд по Vault](vault.md) для синтаксиса `path:key`, специфики KV v2
и паттернов обновления токенов.

## RetryingBackend

Оборачивает любой другой бэкенд. Ретраит только `TransientError`
(таймауты, throttling, 5xx). Auth и not-found не ретраятся.

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

`total_timeout` — жёсткий wall-clock бюджет. Даже если `max_attempts`
позволяло бы больше, vaultly остановит ретраи по бюджету. Это
предотвращает превращение 30-минутного outage в 30-минутный hang при
старте.

См. [Ретраи и stale-on-error](retries-and-stale.md) о взаимодействии
ретраев, TTL и опции `stale_on_error` модели.
