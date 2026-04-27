# Ошибки

vaultly поднимает небольшую сфокусированную иерархию исключений. Любая
ошибка, которую может произвести бэкенд, маппится в одно из них —
включая SDK-специфичные (boto3 `ClientError`, hvac `VaultError`).

```text
VaultlyError
├── ConfigError
│   └── MissingContextVariableError
├── SecretNotFoundError       # не ретраится
├── AuthError                 # не ретраится
└── TransientError            # ретраится RetryingBackend
```

## Ловля ошибок

Зонтичная база — то, что обычно нужно прикладному коду:

```python
from vaultly import VaultlyError

try:
    config.db_password
except VaultlyError as e:
    log.error("secrets failed: %s", e)
    raise
```

Для более тонкого контроля ловите подклассы:

```python
from vaultly import AuthError, SecretNotFoundError, TransientError

try:
    config.db_password
except AuthError:
    # проблема с credentials — алерт ops
    ...
except SecretNotFoundError:
    # баг конфигурации — лог и crash
    ...
except TransientError:
    # бэкенд флапает — RetryingBackend уже исчерпал ретраи
    ...
```

## Каждая ошибка детально

### `VaultlyError`

База для всех исключений vaultly. Наследует `Exception`. Ловите её, если
хотите один блок, обрабатывающий все провалы загрузки секретов.

### `ConfigError`

Оборачивает всё, что является проблемой программирования/конфигурации,
а не бэкенда:

- Ошибка каста: `int("not-a-number")`, `json.loads("{")` (оригинальный
  `ValueError` / `JSONDecodeError` сохраняется как `__cause__`)
- Callable `transform=...`, который кинул исключение
- `prefetch()` / `_fetch` без переданного `backend=`

Не ретраится.

### `MissingContextVariableError`

Подкласс `ConfigError`. Поднимается, когда путь `Secret(...)` ссылается
на `{var}`, не резолвящуюся в поле корневой модели:

- При конструировании (`validate="paths"` — дефолт)
- При фетче, если валидация была отложена (standalone-вложенная модель)
  или пропущена (`validate="none"`)

Сообщение исключения идентифицирует поле и недостающую переменную.

### `SecretNotFoundError`

Бэкенд подтвердил: секрет/ключ/путь не существует. Отличается от
`AuthError` (запрещено) — это эквивалент 404.

`RetryingBackend` **не** ретраит — значение не появится от долбёжки в
бэкенд.

### `AuthError`

Бэкенд отверг наши credentials, токен или IAM-identity. Отличается от
`SecretNotFoundError` — путь может существовать, нам просто нельзя
читать.

`RetryingBackend` **не** ретраит. `VaultBackend` с настроенным
`token_factory=` *попытается* one-shot обновить токен на `Unauthorized`
перед поднятием.

### `TransientError`

Ретраеспособный сбой бэкенда: таймауты, throttling, 5xx, сетевые сбои.
`RetryingBackend` ретраит с экспоненциальным backoff'ом до
`max_attempts` и `total_timeout`.

Бэкенды используют эту категорию широко — что не явно auth-or-not-found,
идёт сюда, чтобы retry-слой сам решал.

## А мой кастомный бэкенд?

Маппьте типы исключений вашего SDK в четыре листовые категории.
Ориентир:

| Категория SDK                      | Маппится в               |
| ---------------------------------- | ------------------------ |
| 404 / `KeyError` / "not found"     | `SecretNotFoundError`    |
| 401 / 403 / "permission denied"    | `AuthError`              |
| 5xx / таймауты / DNS / TCP resets  | `TransientError`         |
| Что-то неоднозначное               | `TransientError` (пусть retry-слой решит) |

Никогда не выпускайте сырое SDK-исключение из `Backend.get`. Код
вызывающего ловит `VaultlyError` и больше ничего не ожидает.
