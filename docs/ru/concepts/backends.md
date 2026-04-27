# Бэкенды

Бэкенд — это то, что реально достаёт секрет. vaultly содержит несколько
встроенных и позволяет писать свои.

## Абстрактный базовый класс `Backend`

```python
from abc import ABC, abstractmethod


class Backend(ABC):
    @abstractmethod
    def get(self, path: str, *, version: int | str | None = None) -> str:
        """Вернуть сырую строку по `path` или поднять ошибку vaultly."""

    def get_batch(self, paths: list[str]) -> dict[str, str]:
        """Прочитать много путей за раз. По умолчанию: серия `get`. Дедуп входов."""
```

Два метода, оба возвращают строки. `SecretModel` сам кастует строки в
тип, объявленный для поля. Бэкенды НЕ занимаются преобразованием типов.

`get_batch` использует `prefetch()`. Дефолтная реализация делает серию
последовательных `get`. Бэкенды с реальным batch API (SSM
`GetParameters`, Vault list, …) переопределяют его для эффективности.

## Встроенные бэкенды

| Бэкенд            | Источник / SDK   | Use case                                        |
| ----------------- | ---------------- | ----------------------------------------------- |
| `EnvBackend`      | `os.environ`     | Локальная разработка, простые деплои, контейнеры. |
| `MockBackend`     | in-memory dict   | Тесты. Трекает вызовы для ассертов.             |
| `AWSSSMBackend`   | `boto3` SSM      | AWS Systems Manager Parameter Store.            |
| `VaultBackend`    | `hvac` KV v2     | HashiCorp Vault.                                |
| `RetryingBackend` | оборачивает любой | Экспоненциальные ретраи на `TransientError`.   |

Облачные бэкенды требуют опциональных установок: `pip install
'vaultly[aws]'` или `pip install 'vaultly[vault]'`.

## Выбор

Дерево решений — в [Выбор бэкенда](../guides/choosing-a-backend.md).
Кратко:

- **Локальная разработка** → `EnvBackend`
- **Тесты** → `MockBackend`
- **AWS-сервис** → `AWSSSMBackend` (скорее всего обёрнутый в
  `RetryingBackend`)
- **Шоп на Vault** → `VaultBackend(token_factory=...)` (скорее всего
  обёрнутый в `RetryingBackend`)

## Ошибки

Каждый встроенный бэкенд маппит свои SDK-исключения в одно из:

- `SecretNotFoundError` — ключ/путь не существует. Не ретраится.
- `AuthError` — невалидные credentials. Не ретраится.
- `TransientError` — таймаут, throttling, 5xx. Ретраится `RetryingBackend`.

Ваш кастомный бэкенд должен следовать той же конвенции. Код в hot-path
сервиса ловит `vaultly.VaultlyError` (umbrella) и более специфичные
подклассы по необходимости.

Полная иерархия — в [Ошибки](errors.md).

## Свой бэкенд

Наследуйте `Backend` и реализуйте `get`. Переопределите `get_batch`,
если у вас есть реальный bulk-fetch API.

```python
from vaultly import Backend
from vaultly.errors import SecretNotFoundError, TransientError, AuthError


class MyBackend(Backend):
    def __init__(self, my_client):
        self._client = my_client

    def get(self, path: str, *, version: int | str | None = None) -> str:
        try:
            return self._client.read(path, version=version)
        except MyClientNotFound as e:
            raise SecretNotFoundError(f"missing: {path}") from e
        except MyClientForbidden as e:
            raise AuthError(f"denied: {path}") from e
        except (MyClientTimeout, MyClient5xx) as e:
            raise TransientError(f"transient: {path}: {e}") from e
```

Затем подключаете к `SecretModel` как любой встроенный:

```python
config = AppConfig(stage="prod", backend=MyBackend(client))
```

Кэширование, ретраи, маскирование vaultly применяются прозрачно.
