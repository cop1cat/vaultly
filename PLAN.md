# vaultly — design & plan

Декларативный Pydantic-совместимый менеджер секретов для Python 3.12+.
Цель: нулевая точка входа — в одной модели можно мешать обычные pydantic-поля
и секреты, не думая про backend в простых случаях.

## Ключевые решения

### Модель

- `SecretModel(pydantic.BaseModel)` — наследник Pydantic v2. Юзер может
  использовать валидаторы, обычные поля, вложенные модели — всё как у Pydantic.
- Секреты объявляются через присваивание: `field: T = Secret(path, ...)`.
  Для mypy/IDE поле имеет тип `T`; `Secret(...)` возвращает Pydantic
  `FieldInfo` с сентинел-дефолтом, так что поле не required при
  конструировании и на уровне рантайма, и на уровне type checker.
  В спайке подтверждено: pyright проходит без плагинов, mypy — с
  официальным `pydantic.mypy`.

  Почему не `Annotated[T, Secret(...)]` — pyright/mypy без плагина не
  распознают `Secret(...)` в метадате как источник дефолта (не `FieldInfo`
  по типу возврата; FieldInfo подкласс нельзя — класс `@final`), и
  секретные поля оказываются required при конструировании. Форма с
  присваиванием этот барьер обходит, потому что любой `= <expr>` в теле
  dataclass-transform-класса уже делает поле опциональным.
- Доступ к полю прозрачный: `config.db_password` возвращает `str`, lazy fetch
  на первом обращении. Никаких `.get_value()`.
- `repr(config)` и `config.model_dump()` маскируют секреты как `"***"`.

```python
class AppConfig(SecretModel):
    stage: str = "dev"
    debug: bool = False
    db_password: str = Secret("/db/{stage}/password", ttl=300)
    api_key: str = Secret("/services/openai/key")

config = AppConfig(stage="prod", backend=AWSSSMBackend(region="eu-west-1"))
```

`backend=` — публичный kwarg у `__init__`, не приватный.

### Контекст (интерполяция путей)

- `{var}` в путях резолвится из полей **самой модели** — отдельный
  `SecretContext` не нужен.
- Для вложенных моделей контекст наследуется от корневой модели через
  `_parent` ссылку. Дочерняя модель сама по себе путь не резолвит —
  даже если `{var}` совпадает с именем поля дочерней модели, значение
  берётся из корневой. Это сознательное упрощение: один источник правды для
  контекста, никаких коллизий между уровнями.

  ```python
  class ServiceConfig(SecretModel):
      api_key: str = Secret("/services/{stage}/{name}/key")
      # {stage} и {name} — из корневой AppConfig, НЕ из полей ServiceConfig

  class AppConfig(SecretModel):
      stage: str
      name: str
      service: ServiceConfig
  ```
- При init собираются все `{vars}` из всех путей (рекурсивно) и сверяются с
  полями корневой модели. Если переменной нет — `MissingContextVariableError`
  немедленно, с понятным сообщением.

### Backend

- Передаётся явно: `AppConfig(stage="prod", backend=AWSSSMBackend(region=...))`.
  Без глобального state, без thread-local, без env-магии auto-detect.
  **Решение (22.04.2026):** env-based auto_backend отброшен как нестабильный.
- Каждый backend сам управляет аутентификацией через стандартные SDK
  credential chains. Пользователь задаёт только то, что специфично для его
  инсталляции (region, mount point, url).
- Protocol:
  ```python
  class Backend(Protocol):
      def get(self, path: str) -> str: ...
      def get_batch(self, paths: list[str]) -> dict[str, str]: ...
  ```
  Дефолтный `get_batch` — N вызовов `get`, бэкенды переопределяют при наличии
  batch API.

### Validation modes

Флаг `validate=` в `__init__`:

- `"none"` — полностью lazy. Ничего не проверяется до первого обращения.
- `"paths"` (default) — при init проверить что все `{vars}` покрыты полями
  модели. В backend не ходим.
- `"fetch"` — prefetch всех секретов через `backend.get_batch()`. Failfast
  при старте сервиса.

### Кэш

- TTL cache per-path, thread-safe.
- `ttl=None` (default) — fetch один раз, далее из кэша навсегда.
- `ttl=0` — кэш отключён, каждое обращение идёт в backend.
- `ttl>0` — значение живёт указанное число секунд.
- `config.refresh("field")` — инвалидировать и перечитать одно поле.
- `config.refresh_all()` — инвалидировать и перечитать всё.

### Ошибки и ретраи

Иерархия:

```
vaultlyError
├── ConfigError
│   └── MissingContextVariableError
├── SecretNotFoundError       # не ретраим
├── AuthError                 # не ретраим
└── TransientError            # ретраим (таймауты, 5xx, throttling)
```

Каждый backend маппит свои exceptions на эту иерархию.

Ретраи — два уровня, не комбинировать:

- **Transport-уровень**: SDK-ретраи внутри backend (boto3 `Config`, hvac, …).
  Работают на сырых HTTP/network-ошибках до того, как они поднимутся к нам.
  Покрывает базовые кейсы.
- **Semantic-уровень**: wrapper `RetryingBackend(inner, max_attempts=3, backoff=...)`
  ретраит уже поднятый `TransientError`. Юзер включает явно.
- Рекомендация: включать что-то одно. Если оба — эффективный бюджет ретраев
  перемножается, и старт сервиса может висеть намного дольше ожидаемого.
- Дефолты `RetryingBackend`: 3 попытки, exp backoff (0.5 → 1 → 2 → 4с),
  total budget ~10с. **Без** бесконечных ретраев при старте.
- Без circuit breaker в v0.1.

### Stale-on-error

- Опция `stale_on_error: bool = False` на модели.
- Если backend возвращает `TransientError`, а в кэше есть истёкшее
  значение — вернуть его с warning-логом.
- Opt-in: по умолчанию выключено (могут быть security-причины).

### Касты

Типы в `field: T = Secret(...)` кастуются автоматически:
- `str` — без изменений
- `int` / `float` — прямой каст
- `bool` — `"true"/"1"/"yes"/"on"` (case-insensitive) → `True`;
  `"false"/"0"/"no"/"off"` → `False`; прочее — `ValueError`.
- `dict` / `list` — `json.loads(value)`

Кастомный `transform: Callable[[str], T]` в `Secret(...)` переопределяет
дефолтный каст.

## Scope v0.1

**В ядре:**
- `SecretModel`, `Secret`, pydantic-интеграция
- Интерполяция путей из полей модели + наследование во вложенных
- TTL cache, refresh/refresh_all
- Маскирование в repr / model_dump
- Иерархия ошибок
- `validate` modes
- `RetryingBackend` wrapper
- `stale_on_error`

**Бэкенды v0.1:**
- `EnvBackend` — локалка, простые деплои
- `MockBackend` — тесты (dict и YAML-фикстуры)
- `AWSSSMBackend` — через `boto3`, batch через `get_parameters` (до 10
  параметров на запрос, пагинировать). Тесты через `moto`.
- `VaultBackend` — через `hvac`, KV v2. Тесты через docker-контейнер в
  dev-mode или `hvac`-мок (определиться на шаге).

**Отложено (v0.2+):**
- `RotatingSecret` — решается через TTL + ротацию на стороне backend
- `AuditHandler` — для v0.1 достаточно `logger.debug/info`
- Async (`aget_value`, `AsyncBackend`) — по запросу
- `Secret[Path]` / `as_tempfile()` — рецепт в README
- `version`, `alias`, `description` на Secret
- `auto_backend()` по env — нестабильно
- Остальные бэкенды: AWS Secrets Manager, Azure KV, GCP SM

## Структура проекта

```
vaultly/
├── pyproject.toml
├── README.md
├── PLAN.md                      # этот файл
├── spike.py                     # референс работающего прототипа
├── src/vaultly/
│   ├── __init__.py              # публичный API
│   ├── errors.py                # иерархия ошибок
│   ├── core/
│   │   ├── secret.py            # Secret metadata class
│   │   ├── model.py             # SecretModel
│   │   ├── cache.py             # TTL cache
│   │   └── casts.py             # str -> T касты
│   ├── backends/
│   │   ├── base.py              # Backend Protocol
│   │   ├── env.py               # EnvBackend
│   │   ├── aws_ssm.py           # AWSSSMBackend (extras: [aws])
│   │   ├── vault.py             # VaultBackend (extras: [vault])
│   │   └── retrying.py          # RetryingBackend wrapper
│   └── testing/
│       └── mock.py              # MockBackend + pytest fixtures
└── tests/
    ├── conftest.py
    ├── test_secret.py
    ├── test_model.py
    ├── test_context.py
    ├── test_cache.py
    ├── test_casts.py
    ├── test_errors.py
    ├── test_retrying.py
    └── backends/
        ├── test_env.py
        ├── test_mock.py
        ├── test_aws_ssm.py      # через moto
        └── test_vault.py        # через docker-dev-mode или hvac-мок
```

## Порядок реализации

1. Scaffolding: `pyproject.toml`, директории, `errors.py`.
2. `Backend` Protocol + `EnvBackend` + `MockBackend`.
3. TTLCache.
4. Касты (`casts.py`).
5. `Secret` metadata class.
6. `SecretModel` на базе Pydantic:
   - `Annotated` парсинг при subclass/init
   - валидация путей (`validate="paths"`)
   - lazy fetch через `__getattribute__`
   - маскирование repr/model_dump
   - наследование контекста во вложенных моделях
   - `refresh` / `refresh_all`
7. `validate="fetch"` + `get_batch`.
8. `stale_on_error`.
9. `RetryingBackend` wrapper.
10. `AWSSSMBackend` (с batch API, SDK-ретраи).
11. `VaultBackend` (KV v2, hvac).
12. README, примеры.

Тесты идут параллельно каждому шагу. `MockBackend` + `EnvBackend` покрывают
всё до шага 10.

## Технические риски

- **Pydantic v2 + `__getattribute__`**: спайк показал что работает, но при
  добавлении валидаторов/computed fields возможны конфликты. Держать
  `__getattribute__` максимально узким (только secret-поля).
- **Type checker vs API формы `Secret`**: спайк прошёл через несколько
  форм и остановился на `field: T = Secret(...)` без своего
  `@dataclass_transform`. Краткий лог:
  - `Annotated[T, Secret(...)]` — pyright видит поле как required
    (не распознаёт `Secret(...)` в metadata как донора дефолта);
  - `Secret` как подкласс `FieldInfo` — запрещено, класс `@final`;
  - `field: T = Secret(...)` + наш `@dataclass_transform` на
    `SecretModel` — pyright зелёный, но `pydantic.mypy` ломается
    (конфликт с pydantic-ным transform'ом на `ModelMetaclass`, поля
    `SecretModel` теряются из синтезированного init);
  - `field: T = Secret(...)` **без** своего `@dataclass_transform` —
    pydantic-ный transform покрывает всё: **pyright 0 errors, mypy +
    `pydantic.mypy` 0 errors, рантайм зелёный**. Итог: ничего лишнего
    не навешиваем, полагаемся на встроенный transform от Pydantic.
- **Наследование контекста во вложенных моделях**: Pydantic валидирует
  вложенные модели при init корневой — нужно подменить backend/parent ссылку
  после валидации через `model_post_init`.

## Открытые вопросы

- Нужен ли config для логирования (имя логгера, verbosity) на уровне модели
  или через стандартный `logging.getLogger("vaultly")`?
- Как тестировать `AWSSSMBackend` — через `moto` или записанные фикстуры?
- Как тестировать `VaultBackend` — docker-контейнер в `-dev` режиме
  (реалистичнее, но требует docker в CI) vs мокировать `hvac.Client`
  (быстрее, но слабее покрытие)?
- Нужен ли свой mypy/pyright-плагин? По итогам спайка: **нет** для v0.1.
  Форма `field: T = Secret(...)` + `@dataclass_transform` на `SecretModel`
  даёт зелёный pyright из коробки. Для mypy в README указать
  `plugins = pydantic.mypy`. Свой плагин — отложить, пока не появятся
  реальные пользовательские жалобы.
