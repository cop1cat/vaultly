# vaultly

**Декларативный, Pydantic-нативный менеджер секретов для Python 3.12+.**

Совмещайте обычные поля Pydantic с полями-секретами в одной модели. Секреты
загружаются лениво при первом обращении, кэшируются с per-field TTL,
маскируются в `repr` и `model_dump`, и всегда имеют тот тип, который вы
объявили.

```python
from vaultly import Secret, SecretModel
from vaultly.backends.aws_ssm import AWSSSMBackend


class AppConfig(SecretModel):
    stage: str = "dev"
    debug: bool = False
    db_password: str = Secret("/db/{stage}/password", ttl=300)
    api_key: str = Secret("/services/openai/key")
    max_conns: int = Secret("/db/{stage}/max_conns")


config = AppConfig(stage="prod", backend=AWSSSMBackend(region_name="eu-west-1"))

config.db_password   # -> str, фетчится при первом обращении, кэш на 300с
config.max_conns     # -> int, скастован из "42"
config.model_dump()  # -> {..., "db_password": "***", "api_key": "***"}
```

## Зачем

Загрузка секретов в большинстве приложений — клубок из `os.getenv`,
вендорских клиентов, кастомного кэширования и комментариев `# TODO:
ротация`. vaultly сжимает это до одной Pydantic-модели, которая:

- Работает с системой типов, которой вы уже пользуетесь — `cfg.db_password`
  это обычный `str`, `cfg.max_conns` — настоящий `int`. Зависимым библиотекам
  (psycopg, httpx, Redis-клиентам) не нужны адаптеры.
- Не утекает — `repr`, `str`, `model_dump`, JSON-вывод маскируют каждое
  секретное поле. `copy.copy`, `copy.deepcopy`, `model_copy` и `pickle`
  отказываются работать с моделью, держащей кэш секретов.
- Явно описывает ретраи и TTL — никакого сюрпризного поведения при буре
  5xx, никакого сюрприза в полночь, когда истекает TTL.
- Тестируема — in-memory `MockBackend` подключается так же, как настоящие
  бэкенды, с трекингом вызовов для ассертов.

## Статус

Pre-1.0. Публичная поверхность стабильна для задокументированных бэкендов;
часть внутренностей (особенно сигнатура `Backend.get`) может измениться до
1.0. См. [changelog](https://github.com/cop1cat/vaultly/blob/main/CHANGELOG.md)
и заметку про breaking-change policy в конце сайта.

## С чего начать

- Здесь впервые? → [Быстрый старт](getting-started/quickstart.md)
- Переходите с `pydantic-settings`? → [Концепции SecretModel](concepts/secret-model.md)
- Выбираете бэкенд? → [Выбор бэкенда](guides/choosing-a-backend.md)
- Работаете в проде? → [Модель безопасности](guides/security-model.md) и
  [Конкурентность](guides/concurrency.md)
