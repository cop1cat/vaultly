# Бэкенды

Бэкенд — это то, что реально достаёт секрет из хранилища. В vaultly
есть несколько готовых, и вы можете написать свой.

## Базовый класс `Backend`

```python
from abc import ABC, abstractmethod


class Backend(ABC):
    @abstractmethod
    def get(self, path: str, *, version: int | str | None = None) -> str:
        """Вернуть сырую строку по `path` или поднять ошибку vaultly."""

    def get_batch(self, paths: list[str]) -> dict[str, str]:
        """Прочитать много путей за раз. По умолчанию — серия `get`. Дедуп входов."""
```

Два метода, оба возвращают строки. `SecretModel` сам приведёт строки к
типу поля. Бэкенды НЕ занимаются приведением типов.

`get_batch` использует `prefetch()`. По умолчанию вызывает `get` в
цикле; бэкенды с реальным batch-API (SSM `GetParameters`, Vault list,
…) переопределяют его для эффективности.

## Встроенные бэкенды

| Бэкенд            | Источник         | Когда использовать                              |
| ----------------- | ---------------- | ----------------------------------------------- |
| `EnvBackend`      | `os.environ`     | Локальная разработка, простые деплои.           |
| `MockBackend`     | in-memory dict   | Тесты. Ведёт журнал вызовов.                    |
| `AWSSSMBackend`   | `boto3` SSM      | AWS Systems Manager Parameter Store.            |
| `VaultBackend`    | `hvac` KV v2     | HashiCorp Vault.                                |
| `RetryingBackend` | оборачивает любой | Экспоненциальные ретраи на `TransientError`.   |

Облачные бэкенды требуют отдельную установку: `pip install
'vaultly[aws]'` или `pip install 'vaultly[vault]'`.

## Какой выбрать

Дерево решений — в [Выборе бэкенда](../guides/choosing-a-backend.md).
Кратко:

- **Локальная разработка** → `EnvBackend`
- **Тесты** → `MockBackend`
- **Сервис на AWS** → `AWSSSMBackend` (как правило, в обёртке
  `RetryingBackend`)
- **Vault-ориентированная инфра** → `VaultBackend(token_factory=...)`
  (тоже в обёртке `RetryingBackend`)

## Ошибки

Каждый встроенный бэкенд приводит свои SDK-ошибки к одному из:

- `SecretNotFoundError` — ключ/путь не существует. Не ретраится.
- `AuthError` — невалидные учётные данные. Не ретраится.
- `TransientError` — таймаут, throttling, 5xx. Ретраится
  `RetryingBackend`.

Свой бэкенд должен следовать тому же контракту. Прикладной код в
hot-path ловит `vaultly.VaultlyError` (общая база) и более узкие
подклассы по необходимости.

Полная иерархия — в [Ошибках](errors.md).

## Свой бэкенд

Наследуйтесь от `Backend` и реализуйте `get`. Переопределите
`get_batch`, если у вашего хранилища есть реальный bulk-fetch API.

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

Подключаете его к `SecretModel` так же, как любой встроенный:

```python
config = AppConfig(stage="prod", backend=MyBackend(client))
```

Кэширование, ретраи и маскирование от vaultly работают прозрачно.
