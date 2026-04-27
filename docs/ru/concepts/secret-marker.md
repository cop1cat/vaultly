# `Secret(...)`

`Secret(...)` — маркер, который превращает обычное поле в секретное.
Используется в правой части декларации:

```python
from vaultly import Secret, SecretModel


class App(SecretModel):
    db_password: str = Secret("/db/password", ttl=60)
```

Возвращает Pydantic `FieldInfo` со специальным sentinel-значением, поэтому
поле необязательно при конструировании. Параметры `path`, `ttl`,
`transform`, `version`, `description` сохраняются как метаданные —
модель читает их на этапе создания класса.

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

Путь к секрету в бэкенде. Плейсхолдеры `{var}` подставляются на этапе
фетча из не-секретных полей **корневой** `SecretModel`.

```python
db_password: str = Secret("/{stage}/db/password")
```

Подробности — в [Интерполяции путей](path-interpolation.md).

### `ttl`

Сколько хранится значение в кэше:

| Значение         | Поведение                                               |
| ---------------- | ------------------------------------------------------- |
| `None` (default) | Кэшировать навсегда.                                    |
| `0`              | Не кэшировать — каждое чтение идёт в бэкенд.            |
| `> 0`            | Количество секунд.                                      |

У каждого пути свой TTL. Версионированные и не-версионированные чтения
одного пути кэшируются отдельно.

### `transform`

Кастомный `Callable[[str], T]`, который полностью заменяет дефолтные
правила приведения типов:

```python
import json
import base64

class App(SecretModel):
    # base64 + JSON → Python-объект
    config: dict = Secret(
        "/app/config-b64",
        transform=lambda s: json.loads(base64.b64decode(s)),
    )
```

Если задан `transform`, аннотация поля никак не используется — за
приведение отвечает только ваш callable. Исключения внутри `transform`
оборачиваются в `ConfigError` с оригиналом в `__cause__`.

### `version`

Закрепить секрет на конкретной версии. Бэкенды, поддерживающие
версионирование, передают это значение по своим правилам:

| Бэкенд            | Как передаётся                                      |
| ----------------- | --------------------------------------------------- |
| `AWSSSMBackend`   | Добавляется `:N` к имени параметра.                 |
| `VaultBackend`    | Передаётся как `version=N` в KV v2.                 |
| `EnvBackend`      | Игнорируется (env vars не версионируются).         |

Версионированный и не-версионированный фетч одного пути кэшируются
отдельно.

### `description`

Свободный текст. Появляется в сообщениях об ошибках и удобен в больших
моделях: `MissingContextVariableError: secret field App.db (postgres prod
credentials)` отлаживается лучше, чем просто `App.db`.

## Что `Secret(...)` НЕ поддерживает

- `Secret` — **не** Python-тип. Нельзя писать `db_password: Secret(str)`
  или `db_password: Secret[str]`. Форма всегда: `field_name: PythonType
  = Secret("/path", ...)`.
- Это не Pydantic-алиас. Другие метаданные Pydantic-полей
  (`Field(min_length=...)`, валидаторы) с `Secret` не комбинируются —
  vaultly владеет сериализацией и валидацией поля.
- Один `Secret(...)` на одно поле. Несколько маркеров не поддерживаются.

## Дефолтные правила приведения типов

Когда `transform` не задан, аннотация поля управляет приведением:

| Аннотация               | Что делает                                                  |
| ----------------------- | ----------------------------------------------------------- |
| `str`                   | без изменений                                               |
| `int` / `float`         | прямой каст                                                 |
| `bool`                  | `true/1/yes/on` ↔ `false/0/no/off` (case-insensitive)       |
| `dict` / `list`         | `json.loads`                                                |
| `T \| None` (Optional)  | разворачивается в `T`, далее правило для `T`                |
| другое                  | сырая строка                                                |

Ошибка приведения поднимает `ConfigError`, не оригинальный тип
исключения (`ValueError`, `JSONDecodeError`, …). Код с
`except VaultlyError` поймает его надёжно.
