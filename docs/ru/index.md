# vaultly

**Декларативный менеджер секретов для Python 3.12+ на базе Pydantic.**

Можно держать в одной модели обычные поля Pydantic и поля-секреты.
Секреты подгружаются лениво, кэшируются с per-field TTL, маскируются в
`repr` и `model_dump`, и приходят в том типе, который вы объявили.

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

config.db_password   # str, тянется при первом обращении, кэшируется на 5 минут
config.max_conns     # int, скастован из "42"
config.model_dump()  # {..., "db_password": "***", "api_key": "***"}
```

## Зачем

В обычном сервисе загрузка секретов — это смесь из `os.getenv`, клиентов
вендорских SDK, своего кэша, и комментариев `# TODO: ротация`. vaultly
сводит всё это к одной модели Pydantic, в которой:

- Типы — те же, что и в коде ниже. `cfg.db_password` — обычный `str`,
  `cfg.max_conns` — обычный `int`. Драйверы БД, HTTP-клиенты, Redis
  работают без обёрток.
- Секреты не утекают: `repr`, `str`, `model_dump`, JSON — всё маскируется
  как `"***"`. `copy.copy`, `copy.deepcopy`, `model_copy`, `pickle`
  отказываются работать с моделью, в которой кэшированы секреты.
- Поведение под нагрузкой предсказуемо: понятные ретраи, понятный TTL,
  никаких сюрпризов в три ночи.
- Удобно тестировать: `MockBackend` — тот же интерфейс, что и боевые
  бэкенды, плюс журнал вызовов для ассертов.

## Статус

До 1.0. Публичный API стабилен в рамках задокументированных бэкендов;
часть внутренних деталей (в первую очередь сигнатура `Backend.get`)
может измениться. См.
[changelog](https://github.com/cop1cat/vaultly/blob/main/CHANGELOG.md)
и заметку про breaking changes в конце сайта.

## С чего начать

- Впервые здесь? → [Быстрый старт](getting-started/quickstart.md)
- Переходите с `pydantic-settings`? → [Концепции SecretModel](concepts/secret-model.md)
- Выбираете бэкенд? → [Выбор бэкенда](guides/choosing-a-backend.md)
- Готовите к проду? → [Модель безопасности](guides/security-model.md) и
  [Конкурентность](guides/concurrency.md)
