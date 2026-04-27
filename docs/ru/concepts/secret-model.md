# SecretModel

`SecretModel` — базовый класс, от которого наследуются пользователи. Это
тонкая надстройка над `pydantic.BaseModel`, добавляющая:

- Детекцию полей-секретов, объявленных через `Secret(...)`
- Ленивый фетч при первом обращении к атрибуту
- Общий, thread-safe TTL-кэш
- Маскирование в `repr` / `model_dump` / JSON
- Валидацию интерполяции путей при конструировании

Всё остальное — типы полей, валидаторы, `model_config`, наследование —
ведёт себя ровно как Pydantic. Можно мешать поля vaultly с обычными
полями Pydantic, computed fields, валидаторами и т.д.

## Жизненный цикл

```python
class App(SecretModel):
    stage: str
    db_password: str = Secret("/db/{stage}/password", ttl=60)
```

При вызове `App(stage="prod", backend=...)`:

1. **Pydantic валидирует входы** как обычно (`stage` должен быть `str`).
2. **Запускается `model_validator(mode='after')` от vaultly**:
    - Подвязывает вложенные `SecretModel`-дети к этому корню (так чтобы
      они делили его кэш и бэкенд).
    - Обходит каждый путь `Secret(...)` и проверяет, что каждая `{var}`
      резолвится в реальное не-секретное поле корневой модели.
    - Если на классе указано `validate="fetch"` — вызывает `prefetch()`,
      который активно заполняет кэш из бэкенда.
3. **Конструктор возвращает** — ни одно секретное значение пока не было
   прочитано (если вы не включили prefetch).

При обращении к секретному полю (`app.db_password`):

1. `SecretModel.__getattribute__` замечает, что `db_password` —
   секретное поле.
2. Шаблон пути заполняется текущими скалярными полями модели.
3. Проверяется кэш — при попадании сразу возвращается значение.
4. При промахе берётся per-key fetch lock; бэкенд вызывается ровно один
   раз даже если 100 потоков спрашивают параллельно.
5. Сырая строка кастуется в тип, объявленный для поля (`str`, `int`,
   `bool`, `dict`, `list` или то, что выдаёт пользовательский
   `transform=`).
6. Значение кладётся в кэш и возвращается.

## Конфигурация

Настройки уровня подкласса задаются через class-kwargs (предпочтительно)
или через ClassVar-ы с подчёркиванием (fallback для старых паттернов):

=== "Class kwargs (предпочтительно)"

    ```python
    class App(SecretModel, validate="fetch", stale_on_error=True):
        ...
    ```

=== "ClassVar (fallback)"

    ```python
    class App(SecretModel):
        _vaultly_validate = "fetch"
        _vaultly_stale_on_error = True
    ```

### `validate`

Что проверять при конструировании:

- `"none"` — не делать ничего. Ошибки всплывают только на первом фетче.
- `"paths"` (по умолчанию) — проверить, что каждая `{var}` резолвится
  в поле корня. Дёшево, ловит опечатки.
- `"fetch"` — дополнительно вызвать `prefetch()` и прочитать каждый
  секрет. Ловит missing/auth-проблемы при старте, ценой одного дополнительного
  round-trip к бэкенду при конструировании.

### `stale_on_error`

Если бэкенд кинул `TransientError`, и в кэше есть просроченное значение —
вернуть его с warning-логом вместо исключения.

По умолчанию выключено — в некоторых нагрузках возврат stale-credential
во время outage хуже, чем падение. Включается per-model:

```python
class App(SecretModel, stale_on_error=True):
    ...
```

## Публичный API

| Метод                 | Назначение                                              |
| --------------------- | -------------------------------------------------------- |
| `prefetch()`          | Заранее загрузить все секреты в дереве.                  |
| `refresh(name)`       | Инвалидировать кэш одного поля и перечитать.             |
| `refresh_all()`       | Инвалидировать весь кэш.                                 |

## Что `SecretModel` НЕ поддерживает

Эти намеренно поднимают `NotImplementedError`:

- `model.model_copy()`
- `copy.copy(model)`
- `copy.deepcopy(model)`
- `pickle.dumps(model)`

Каждое из них либо разделило, либо продублировало бы in-memory кэш
(содержащий cleartext-значения секретов) и сломало бы parent/root-связи
во вложенных деревьях. Создавайте свежий экземпляр. См. [гайд по модели
безопасности](../guides/security-model.md).

`model.model_construct(...)` разрешён, но обходит pydantic-валидацию
полностью — включая нашу. Path-валидация и prefetch не выполняются.
Ошибки всплывают лениво на первом фетче. Использовать только если
понимаете, что делаете.

## Поверхность публичного API

```python
from vaultly import (
    SecretModel,         # базовый класс
    Secret,              # маркер поля
    Backend,             # ABC для своих бэкендов
    EnvBackend,          # встроенный: env vars
    MockBackend,         # встроенный: in-memory, для тестов
    RetryingBackend,     # обёртка: ретраи на TransientError
    # типы ошибок
    VaultlyError,
    ConfigError,
    MissingContextVariableError,
    SecretNotFoundError,
    AuthError,
    TransientError,
)
```

Опциональные облачные бэкенды лежат в отдельных подмодулях, чтобы
`import vaultly` работал без их SDK:

```python
from vaultly.backends.aws_ssm import AWSSSMBackend
from vaultly.backends.vault import VaultBackend
```
