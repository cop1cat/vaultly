# AWS SSM Parameter Store

```sh
pip install 'vaultly[aws]'
```

```python
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = AWSSSMBackend(region_name="eu-west-1")
```

## Что делает

- Читает параметры через `boto3.client("ssm").get_parameter(...)` для
  одиночных запросов и `get_parameters(...)` для batch (SSM ограничивает
  batch до 10 имён за вызов; vaultly сам разбивает на чанки, если
  больше).
- Расшифровывает `SecureString` через `WithDecryption=True` (по
  умолчанию; для отключения — `with_decryption=False`).
- Приводит boto3 / botocore исключения к иерархии ошибок vaultly.

## Дефолтный конфиг

Конструктор `AWSSSMBackend(region_name="...")` создаёт `boto3` SSM-клиент
с разумным дефолтным `botocore.Config`:

```python
DEFAULT_CONFIG = Config(
    retries={"mode": "adaptive", "max_attempts": 3},
    connect_timeout=2.0,
    read_timeout=5.0,
)
```

Это защищает от бесконечных hang'ов при сетевом сбое. Для override:

```python
from botocore.config import Config

backend = AWSSSMBackend(
    region_name="eu-west-1",
    config=Config(read_timeout=10.0),
)
```

Или передайте полностью настроенный клиент:

```python
import boto3

client = boto3.client("ssm", config=my_org_config)
backend = AWSSSMBackend(client=client)
```

## Версионирование

SSM хранит каждое изменение как новую версию. Закрепить конкретную:

```python
class App(SecretModel):
    pinned: str = Secret("/db/password", version=2)
    latest: str = Secret("/db/password")
```

vaultly транслирует `version=2` в SSM-синтаксис `Name=/db/password:2`.

## IAM

IAM-роли сервиса нужны `ssm:GetParameter` для одиночных чтений и
`ssm:GetParameters` для batch. Если ваши секреты — `SecureString`
(рекомендуется), также нужен `kms:Decrypt` на нужный KMS-ключ.

Минимальная политика для `prod`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters"],
      "Resource": "arn:aws:ssm:eu-west-1:123:parameter/prod/*"
    },
    {
      "Effect": "Allow",
      "Action": ["kms:Decrypt"],
      "Resource": "arn:aws:kms:eu-west-1:123:key/<key-id>"
    }
  ]
}
```

## Маппинг ошибок

| boto3 / SSM error                                                 | vaultly →              |
| ----------------------------------------------------------------- | ---------------------- |
| `ParameterNotFound`                                               | `SecretNotFoundError`  |
| `AccessDeniedException`, `UnauthorizedAccessException`            | `AuthError`            |
| `ThrottlingException`, `RequestLimitExceeded`, `ServiceUnavailable`, `InternalServerError` | `TransientError` |
| `BotoCoreError` (network)                                         | `TransientError`       |
| Что-то другое (неизвестные `ClientError` коды)                    | `TransientError` (пусть `RetryingBackend` решает) |

## В связке с ретраями

```python
from vaultly import RetryingBackend
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = RetryingBackend(
    AWSSSMBackend(region_name="eu-west-1"),
    max_attempts=3,
    total_timeout=10.0,
)
```

Заметьте: boto3 уже делает ретраи на transport-уровне (через
`Config.retries`). `RetryingBackend` — *семантический* слой сверху,
ретраит уже поднятые `TransientError` от vaultly. Не задирайте оба
бюджета в high — выйдет умножение.

## Рецепты

### Иерархическое multi-stage приложение

```python
class DbConfig(SecretModel):
    password: str = Secret("/{stage}/db/password")
    pool_size: int = Secret("/{stage}/db/pool")

class App(SecretModel, validate="fetch"):
    stage: str
    db: DbConfig
    api_key: str = Secret("/{stage}/api/key")
    flags: dict = Secret("/{stage}/feature_flags")

config = App(stage="prod", db={}, backend=AWSSSMBackend(region_name="eu-west-1"))
```

`validate="fetch"` префетчит всё одним batched-вызовом `GetParameters`
(или двумя, если секретов больше 10). Пропавший или неправильно
проправленный параметр валит деплой при старте, а не позже на
конкретном запросе.

### Локалка и прод одной моделью

```python
import os
from vaultly import EnvBackend
from vaultly.backends.aws_ssm import AWSSSMBackend


def make_backend():
    if os.getenv("ENV") == "local":
        return EnvBackend(prefix="MYAPP")
    return AWSSSMBackend(region_name=os.environ["AWS_REGION"])


config = App(stage=os.environ["STAGE"], backend=make_backend())
```
