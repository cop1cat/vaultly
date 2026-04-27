# Тестирование конфига

Используйте `MockBackend` в unit и integration тестах, проверяющих код,
который потребляет `SecretModel`. Это in-memory dict, имеющий тот же
контракт `Backend`, что и боевые бэкенды, плюс журнал вызовов для
ассертов.

## Быстрый пример

```python
from vaultly import MockBackend, Secret, SecretModel


class App(SecretModel):
    stage: str
    db_password: str = Secret("/{stage}/db/password")
    api_key: str = Secret("/services/openai/key")


def test_app_uses_correct_paths():
    backend = MockBackend(
        {
            "/prod/db/password": "s3cr3t",
            "/services/openai/key": "sk-test",
        }
    )
    app = App(stage="prod", backend=backend)
    assert app.db_password == "s3cr3t"
    assert app.api_key == "sk-test"
    # MockBackend ведёт журнал каждого вызова.
    assert backend.calls == [
        ("/prod/db/password", None),
        ("/services/openai/key", None),
    ]
```

## Ассерты на поведение кэша

`MockBackend.calls` — список кортежей `(path, version)`. Используйте
для проверки кэширования:

```python
def test_repeated_reads_hit_cache():
    backend = MockBackend({"/prod/db/password": "s3cr3t"})
    app = App(stage="prod", backend=backend)

    _ = app.db_password
    _ = app.db_password
    _ = app.db_password

    # Три чтения, один вызов бэкенда — кэш работает.
    assert backend.calls == [("/prod/db/password", None)]


def test_refresh_actually_refetches():
    backend = MockBackend({"/prod/db/password": "v1"})
    app = App(stage="prod", backend=backend)
    _ = app.db_password
    backend.reset_calls()

    backend.data["/prod/db/password"] = "v2"
    assert app.refresh("db_password") == "v2"
    assert backend.calls == [("/prod/db/password", None)]
```

`MockBackend.reset_calls()` чистит журнал вызовов, не трогая данные —
удобно, если в тесте нужен warmup перед основной проверкой:

```python
def test_only_count_post_warmup_calls():
    backend = MockBackend({"/k": "v"})
    app = App(backend=backend)
    _ = app.k             # warmup
    backend.reset_calls() # считаем только то, что после
    app.refresh("k")
    assert backend.calls == [("/k", None)]
```

## Версионированные секреты

Передайте отдельный `versions=`:

```python
backend = MockBackend(
    versions={("/db/password", 2): "older"},
)

class App(SecretModel):
    pinned: str = Secret("/db/password", version=2)

App(backend=backend).pinned == "older"
```

## Тестирование error-paths

`MockBackend` поднимает `SecretNotFoundError` для отсутствующих ключей:

```python
import pytest
from vaultly import SecretNotFoundError

def test_missing_secret_raises():
    backend = MockBackend({})
    app = App(stage="prod", backend=backend)
    with pytest.raises(SecretNotFoundError):
        _ = app.db_password
```

Для тестов retry / stale-on-error пишите свой fault-injecting `Backend`:

```python
from vaultly import Backend, TransientError


class FlakyBackend(Backend):
    def __init__(self, data, fail_first=0):
        self.data = data
        self.fail_first = fail_first
        self.calls = 0

    def get(self, path, *, version=None):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise TransientError("simulated outage")
        return self.data[path]
```

Подключаете в `RetryingBackend` так же, как и в проде.

## Тесты валидации путей

Валидация запускается при конструировании. Опечатки ловятся прямо в
тесте:

```python
import pytest
from vaultly import MissingContextVariableError


def test_typo_in_path_caught_at_construction():
    class Broken(SecretModel):
        stage: str
        x: str = Secret("/{stge}/x")  # опечатка

    with pytest.raises(MissingContextVariableError, match="stge"):
        Broken(stage="prod", backend=MockBackend({}))
```

## End-to-end с реальными бэкендами

Для интеграционных тестов, проверяющих реальный wire-формат, используйте
`moto` для AWS или мок hvac на уровне SDK для Vault. В репозитории
vaultly это лежит в `tests/integration/`.

```python
from moto import mock_aws
import boto3
from vaultly.backends.aws_ssm import AWSSSMBackend


@mock_aws
def test_with_real_ssm_wire_format():
    boto3.client("ssm").put_parameter(
        Name="/test/key", Value="real", Type="SecureString",
    )
    backend = AWSSSMBackend(region_name="us-east-1")
    assert backend.get("/test/key") == "real"
```

## Чего НЕ делать в тестах

- **Не делайте `model_copy` / `pickle`** на тестовых инстансах. Оба
  заблокированы. Создавайте новый инстанс на каждый тест.
- **Не шарьте `MockBackend` между тестами**, если только специально не
  тестируете cross-test кэширование. Каждый тест должен владеть своим
  бэкендом, чтобы ассерты на `calls` оставались чистыми.
- **Не полагайтесь на TTL в тестах с очень короткими TTL**
  (sub-millisecond). Используйте `MockBackend.reset_calls()` и явные
  `refresh()` — это надёжнее, чем гонка с `time.sleep`.
