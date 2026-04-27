# Быстрый старт

Пять минут до рабочего конфига.

## 1. Объявите модель

`SecretModel` — это `BaseModel` от Pydantic с поддержкой полей через
`Secret(...)`. Скаляры и секреты можно мешать в одной модели:

```python
from vaultly import Secret, SecretModel


class AppConfig(SecretModel):
    stage: str = "dev"
    debug: bool = False
    db_password: str = Secret("/db/{stage}/password", ttl=300)
    api_key: str = Secret("/services/openai/key")
    max_conns: int = Secret("/db/{stage}/max_conns")
```

Что здесь важно:

- `db_password: str` — это **тип, с которым вы будете работать**.
  `cfg.db_password` — обычный `str`, не `SecretStr` или прокси.
- `Secret("/db/{stage}/password")` — путь может ссылаться на любое
  не-секретное поле **корневой** модели (здесь это `{stage}`).
- `ttl=300` — кэшировать значение 5 минут. По умолчанию — навсегда.
- `max_conns: int` — vaultly сам приведёт строку из бэкенда к `int`.

## 2. Выберите бэкенд

Локально проще всего через переменные окружения:

```python
from vaultly import EnvBackend
```

`EnvBackend` отображает `/db/prod/password` → `DB_PROD_PASSWORD`. Правила
префиксов — в [гайде по выбору бэкенда](../guides/choosing-a-backend.md).

Для тестов есть in-memory `MockBackend`:

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

Для облачных бэкендов:

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

Валидация путей запускается прямо в конструкторе. Если
`Secret("/{stage}/x")` ссылается на поле, которого нет в модели,
получите `MissingContextVariableError` сразу — а не при первом фетче в
проде.

## 4. Используйте

```python
config.db_password   # "s3cr3t" — фетч из бэкенда, кэш на 300с
config.api_key       # "sk-abc"
config.max_conns     # 20 — приведено к int
config.stage         # "prod" — обычное поле, без обращения к бэкенду
```

Повторные чтения отдаются из кэша; в бэкенд никто не ходит.

## 5. Маскирование в логах и дампах

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

!!! warning "Прямое обращение к атрибуту НЕ маскируется"
    `print(config.db_password)` напечатает реальное значение. Для
    логов используйте `model_dump`-сериализацию. Подробнее — в
    [гайде по модели безопасности](../guides/security-model.md).

## 6. Обновление после ротации

```python
# Кто-то ротировал пароль во внешнем хранилище.
config.refresh("db_password")    # инвалидировать одно поле и перечитать
config.refresh_all()             # очистить весь кэш
```

## 7. Опционально: предзагрузка на старте

Если хотите, чтобы ошибка конфигурации валила деплой сразу, а не
всплывала через три часа:

```python
class AppConfig(SecretModel, validate="fetch"):
    ...

# Конструктор теперь блокируется до фетча всех секретов; любая ошибка
# бэкенда поднимется здесь.
config = AppConfig(stage="prod", backend=backend)
```

## Дальше

- [SecretModel](../concepts/secret-model.md) — полный жизненный цикл от
  объявления до фетча.
- [Интерполяция путей](../concepts/path-interpolation.md) — как
  резолвится `{var}`, в том числе во вложенных моделях.
- [Выбор бэкенда](../guides/choosing-a-backend.md) — что использовать.
