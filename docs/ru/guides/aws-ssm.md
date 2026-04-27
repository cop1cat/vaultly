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
  одиночных фетчей и `get_parameters(...)` для batch-чтений (SSM
  ограничивает batch до 10 имён за вызов; vaultly автоматически чанкит
  большие запросы).
- Расшифровывает `SecureString`-параметры через `WithDecryption=True`
  (default; передайте `with_decryption=False` чтобы выключить).
- Маппит boto3 / botocore исключения в иерархию ошибок vaultly.

## Дефолтный конфиг

`AWSSSMBackend(region_name="...")` создаёт `boto3` SSM-клиент с разумным
дефолтным `botocore.Config`:

```python
DEFAULT_CONFIG = Config(
    retries={"mode": "adaptive", "max_attempts": 3},
    connect_timeout=2.0,
    read_timeout=5.0,
)
```

Это защищает от бесконечных hang-ов при сетевом сбое. Override:

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

IAM-identity, под которой работает приложение, нуждается в
`ssm:GetParameter` для одиночных чтений и `ssm:GetParameters` для batch.
Если ваши секреты — `SecureString` (рекомендуется), также дайте
`kms:Decrypt` на нужный KMS-ключ.

Минимальная least-privilege политика для `prod`:

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

| boto3 / SSM error                                                 | vaultly маппит в       |
| ----------------------------------------------------------------- | ---------------------- |
| `ParameterNotFound`                                               | `SecretNotFoundError`  |
| `AccessDeniedException`, `UnauthorizedAccessException`            | `AuthError`            |
| `ThrottlingException`, `RequestLimitExceeded`, `ServiceUnavailable`, `InternalServerError` | `TransientError` |
| `BotoCoreError` (network)                                         | `TransientError`       |
| Что-то ещё (неизвестные `ClientError` коды)                       | `TransientError` — пусть `RetryingBackend` решает |

## Связка с ретраями

```python
from vaultly import RetryingBackend
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = RetryingBackend(
    AWSSSMBackend(region_name="eu-west-1"),
    max_attempts=3,
    total_timeout=10.0,
)
```

Заметьте, что boto3 уже ретраит на transport-уровне (через
`Config.retries`). `RetryingBackend` — *семантический* слой сверху —
ретраит `TransientError` от vaultly, который срабатывает только после
того, как boto3 сдался. Не задирайте оба бюджета в high — будет
умножение.

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

`validate="fetch"` префетчит всё одним batched `GetParameters`-вызовом
(или двумя если у вас > 10 секретов), и пропавший / mis-permissioned
параметр валит деплой при старте, а не в 3 ночи когда кто-то его читает.

### Локалка + прод одной моделью

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
