# Быстрый старт

Пять минут — и рабочий конфиг.

## 1. Объявите модель

`SecretModel` — это Pydantic `BaseModel` плюс декларация полей через
`Secret(...)`. Скалярные поля и секреты можно свободно мешать:

```python
from vaultly import Secret, SecretModel


class AppConfig(SecretModel):
    stage: str = "dev"
    debug: bool = False
    db_password: str = Secret("/db/{stage}/password", ttl=300)
    api_key: str = Secret("/services/openai/key")
    max_conns: int = Secret("/db/{stage}/max_conns")
```

Несколько моментов:

- `db_password: str` — это **тот тип, с которым вы будете работать**.
  `cfg.db_password` — обычный `str`, не `SecretStr`-обёртка.
- `Secret("/db/{stage}/password")` — путь может ссылаться на любое
  *не-секретное* поле **корневой** модели (тут `{stage}`).
- `ttl=300` — кэшировать значение 5 минут. По умолчанию — «навсегда».
- `max_conns: int` — vaultly сам кастует строку из бэкенда в `int`.

## 2. Выберите бэкенд

Для локальной разработки проще всего env vars:

```python
from vaultly import EnvBackend
```

`EnvBackend` маппит `/db/prod/password` → `DB_PROD_PASSWORD`. (Правила
префиксов — в [гайде по выбору бэкенда](../guides/choosing-a-backend.md).)

Для тестов используйте in-memory `MockBackend`:

```python
from vaultly import MockBackend

backend = MockBackend(
    {
        "/db/prod/password": "s3cr3t",
        "/services/openai/key": "sk-abc",
        "/db/prod/max_conns": "20",
    }
)
```

Для реальных облачных бэкендов:

```python
from vaultly.backends.aws_ssm import AWSSSMBackend
from vaultly.backends.vault import VaultBackend

aws = AWSSSMBackend(region_name="eu-west-1")
vault = VaultBackend(url="https://vault.example.com", token=os.environ["VAULT_TOKEN"])
```

## 3. Создайте модель

```python
config = AppConfig(stage="prod", debug=True, backend=backend)
```

Валидация путей выполняется при конструировании. Если `Secret("/{stage}/x")`
ссылается на имя поля, которого нет в модели, вы получите чёткий
`MissingContextVariableError` сразу — без сюрприза при первом фетче.

## 4. Используйте

```python
config.db_password   # "s3cr3t" — фетч из бэкенда, кэш на 300с
config.api_key       # "sk-abc"
config.max_conns     # 20 — скастовано в int
config.stage         # "prod" — не секрет, без обращения к бэкенду
```

Повторные чтения попадают в кэш; последующие вызовы не идут в бэкенд.

## 5. Маскирование в логах / дампах

```python
print(config)
# > AppConfig(stage='prod', debug=True, db_password='***', api_key='***',
#            max_conns='***')

config.model_dump()
# > {'stage': 'prod', 'debug': True, 'db_password': '***',
#    'api_key': '***', 'max_conns': '***'}

config.model_dump_json()
# > {"stage": "prod", ..., "db_password": "***", ...}
```

!!! warning "Прямой доступ к атрибутам НЕ маскирует"
    `print(config.db_password)` напечатает реальное значение. Для лог-вывода
    используйте `model_dump`-сериализацию. Полная картина — в [гайде про
    модель безопасности](../guides/security-model.md).

## 6. Refresh после ротации

```python
# Оператор ротировал пароль во внешнем хранилище.
config.refresh("db_password")    # инвалидировать + перефетч одного поля
config.refresh_all()              # инвалидировать весь кэш
```

## 7. Опционально: prefetch при старте

Закрепите ошибки на момент старта, чтобы не обнаружить misconfigured-
секрет через три часа после деплоя:

```python
class AppConfig(SecretModel, validate="fetch"):
    ...

# Конструктор теперь блокируется до фетча всех секретов. Любая ошибка
# бэкенда всплывёт сразу.
config = AppConfig(stage="prod", backend=backend)
```

## Дальше

- [Концепции SecretModel](../concepts/secret-model.md) — полный жизненный
  цикл от объявления до фетча.
- [Интерполяция путей](../concepts/path-interpolation.md) — как
  резолвится `{var}`, в том числе во вложенных моделях.
- [Выбор бэкенда](../guides/choosing-a-backend.md) — что использовать когда.
