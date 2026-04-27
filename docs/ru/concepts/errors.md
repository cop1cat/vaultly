# Ошибки

vaultly поднимает компактную иерархию исключений. Любая ошибка, которую
может произвести бэкенд, приводится к одному из них — включая
SDK-специфичные (boto3 `ClientError`, hvac `VaultError`).

```text
VaultlyError
├── ConfigError
│   └── MissingContextVariableError
├── SecretNotFoundError       # не ретраится
├── AuthError                 # не ретраится
└── TransientError            # ретраится RetryingBackend
```

## Как ловить

Общая база — то, что обычно нужно прикладному коду:

```python
from vaultly import VaultlyError

try:
    config.db_password
except VaultlyError as e:
    log.error("secrets failed: %s", e)
    raise
```

Для более тонкого контроля ловите конкретные подклассы:

```python
from vaultly import AuthError, SecretNotFoundError, TransientError

try:
    config.db_password
except AuthError:
    # проблема с учётными данными — алерт
    ...
except SecretNotFoundError:
    # ошибка конфигурации — лог и падаем
    ...
except TransientError:
    # бэкенд флапает — RetryingBackend уже исчерпал ретраи
    ...
```

## Каждая ошибка подробнее

### `VaultlyError`

База для всех исключений vaultly. Наследует `Exception`. Ловите её,
если хотите один блок на все возможные сбои загрузки секретов.

### `ConfigError`

Оборачивает всё, что относится к проблемам кода или конфигурации, а
не бэкенда:

- Ошибка приведения типа: `int("not-a-number")`, `json.loads("{")`
  (оригинальный `ValueError` / `JSONDecodeError` сохраняется в
  `__cause__`)
- Callable `transform=...`, который кинул исключение
- `prefetch()` или `_fetch` без переданного `backend=`

Не ретраится.

### `MissingContextVariableError`

Подкласс `ConfigError`. Поднимается, когда путь `Secret(...)` ссылается
на `{var}`, не резолвящуюся в поле корневой модели:

- При конструировании (`validate="paths"` — поведение по умолчанию)
- При фетче, если валидация была отложена (standalone-вложенная
  модель) или пропущена (`validate="none"`)

Сообщение в исключении называет конкретное поле и недостающую
переменную.

### `SecretNotFoundError`

Бэкенд подтвердил: секрет не существует. Отличается от `AuthError`
(запрещён) — это эквивалент 404.

`RetryingBackend` его **не** ретраит — повторными запросами секрет
не появится.

### `AuthError`

Бэкенд отверг учётные данные / токен / IAM-identity. Отличается от
`SecretNotFoundError` — путь может существовать, просто читать его
нам не разрешено.

`RetryingBackend` его **не** ретраит. `VaultBackend` с настроенным
`token_factory=` *попробует* однократно обновить токен на
`Unauthorized` перед тем, как поднять `AuthError`.

### `TransientError`

Сбой, который имеет смысл повторить: таймаут, throttling, 5xx,
сетевая проблема. `RetryingBackend` ретраит с экспоненциальным
backoff'ом до `max_attempts` и `total_timeout`.

Бэкенды используют эту категорию широко — всё, что не явно
auth-or-not-found, попадает сюда, чтобы retry-слой сам разбирался.

## Что делать в своём бэкенде

Маппьте типы исключений своего SDK в одну из четырёх листовых
категорий. Ориентир:

| Категория SDK                       | Соответствует         |
| ----------------------------------- | --------------------- |
| 404 / `KeyError` / "not found"      | `SecretNotFoundError` |
| 401 / 403 / "permission denied"     | `AuthError`           |
| 5xx / таймауты / DNS / TCP resets   | `TransientError`      |
| Что-то непонятное                   | `TransientError` (пусть retry-слой решает) |

Никогда не выпускайте сырое SDK-исключение наружу из `Backend.get`.
Вызывающий код ждёт `VaultlyError` и больше ничего.
