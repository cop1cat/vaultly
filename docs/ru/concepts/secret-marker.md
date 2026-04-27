# `Secret(...)`

`Secret(...)` — маркер, делающий поле секрет-загружаемым. Используется в
правой части декларации поля:

```python
from vaultly import Secret, SecretModel


class App(SecretModel):
    db_password: str = Secret("/db/password", ttl=60)
```

Возвращает Pydantic `FieldInfo` с sentinel-значением по умолчанию,
поэтому поле необязательно при конструировании. Параметры `path`, `ttl`,
`transform`, `version`, `description` сохраняются как метаданные,
которые модель читает на этапе создания класса.

## Параметры

```python
def Secret(
    path: str,
    *,
    ttl: float | None = None,
    transform: Callable[[str], Any] | None = None,
    version: int | str | None = None,
    description: str | None = None,
) -> Any: ...
```

### `path`

Путь в бэкенде. Плейсхолдеры `{var}` заполняются на этапе фетча из
не-секретных полей **корневой** `SecretModel`.

```python
db_password: str = Secret("/{stage}/db/password")
```

Подробности интерполяции — в [Интерполяция путей](path-interpolation.md).

### `ttl`

Сколько хранится резолвленное значение в per-root кэше:

| Значение         | Поведение                                       |
| ---------------- | ----------------------------------------------- |
| `None` (default) | Кэшировать навсегда (без срока).                |
| `0`              | Не кэшировать — каждое обращение в бэкенд.      |
| `> 0`            | Количество секунд.                              |

Каждый путь имеет свой TTL. Версионированные секреты кэшируются
отдельно от не-версионированных того же пути.

### `transform`

Кастомный `Callable[[str], T]`, полностью переопределяющий дефолтные
правила каста vaultly:

```python
import json
import base64

class App(SecretModel):
    # декодировать base64 + JSON в Python-объект
    config: dict = Secret(
        "/app/config-b64",
        transform=lambda s: json.loads(base64.b64decode(s)),
    )
```

При указании `transform` аннотация поля не управляет приведением — за
каст полностью отвечает callable. Исключения, поднятые `transform`,
оборачиваются в `ConfigError` с оригиналом в `__cause__`.

### `version`

Закрепить секрет на конкретной версии. Бэкенды, поддерживающие
версионирование, передают значение по своим правилам:

| Бэкенд            | Wire-формат                            |
| ----------------- | -------------------------------------- |
| `AWSSSMBackend`   | Добавляет `:N` к имени параметра.      |
| `VaultBackend`    | Передаёт `version=N` в KV v2.          |
| `EnvBackend`      | Игнорирует (env vars не версионируются). |

Версионированные и не-версионированные чтения одного пути кэшируются
отдельно.

### `description`

Свободный текст. Всплывает в сообщениях об ошибках — полезно в больших
моделях, где `MissingContextVariableError: secret field App.db
(postgres prod credentials)` отлаживается лучше, чем
`MissingContextVariableError: secret field App.db`.

## Что `Secret(...)` НЕ поддерживает

- `Secret` — **не** Python-тип. Нельзя писать `db_password: Secret(str)`
  или `db_password: Secret[str]`. Форма всегда: `field_name: PythonType
  = Secret("/path", ...)`.
- Это не Pydantic-алиас. Другие метаданные Pydantic-полей
  (`Field(min_length=...)`, валидаторы) не комбинируются с `Secret` —
  vaultly владеет сериализацией и валидацией поля.
- Несколько секретов на одно поле не поддерживаются. Один `Secret(...)`
  на поле.

## Дефолтные правила каста

Если `transform` не указан, аннотация управляет кастом:

| Аннотация               | Каст                                                        |
| ----------------------- | ----------------------------------------------------------- |
| `str`                   | passthrough                                                 |
| `int` / `float`         | прямой                                                      |
| `bool`                  | `true/1/yes/on` ↔ `false/0/no/off` (case-insensitive)       |
| `dict` / `list`         | `json.loads`                                                |
| `T \| None` (Optional)  | разворачивается в `T`, далее правило для `T`                |
| что-то ещё              | сырая строка                                                |

Ошибка каста поднимает `ConfigError`, никогда не оригинальный тип
(`ValueError`, `JSONDecodeError`, …). Код с `except VaultlyError`
надёжно его поймает.
