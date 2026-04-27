# vaultly

**Менеджер секретов для Python 3.12+ на базе Pydantic.**

Пишете обычную Pydantic-модель, но часть полей объявляете как
`Secret(...)`. Эти поля подгружаются лениво при первом обращении,
кэшируются по TTL и маскируются в `repr` и `model_dump`. В коде ниже
они выглядят как обычные `str` или `int` — никаких прокси и обёрток.

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

config.db_password   # str — подтянется при первом обращении, кэш на 5 минут
config.max_conns     # int — скастован из "42"
config.model_dump()  # {..., "db_password": "***", "api_key": "***"}
```

## Зачем

Загрузка секретов в обычном сервисе — это смесь из `os.getenv`,
SDK конкретных провайдеров, кустарного кэша и комментариев
`# TODO: ротация`. vaultly сводит всё это к одной модели, в которой:

- Типы соответствуют ожиданиям. `cfg.db_password` — обычный `str`,
  `cfg.max_conns` — обычный `int`. Драйверы БД, HTTP-клиенты и Redis
  работают без обёрток.
- Секреты не утекают в логи. `repr`, `str`, `model_dump` и JSON-вывод
  отдают `"***"` вместо значения. `pickle`, `copy.copy`, `copy.deepcopy`
  и `model_copy` запрещены — они бы либо разделили, либо склонировали
  кэш с открытым текстом.
- Ретраи и TTL ведут себя предсказуемо. Жёсткий бюджет на повторы,
  понятная семантика истечения кэша.
- Удобно тестировать. `MockBackend` имеет тот же интерфейс, что и
  боевые бэкенды, и ведёт журнал вызовов для ассертов.

## Статус

До 1.0. Публичный API стабилен в рамках задокументированных бэкендов;
часть внутренних деталей (в первую очередь сигнатура `Backend.get`)
может ещё измениться. См.
[changelog](https://github.com/cop1cat/vaultly/blob/main/CHANGELOG.md)
и заметку про breaking changes в конце сайта.

## С чего начать

- Впервые здесь? → [Быстрый старт](getting-started/quickstart.md)
- Переходите с `pydantic-settings`? → [SecretModel](concepts/secret-model.md)
- Выбираете бэкенд? → [Выбор бэкенда](guides/choosing-a-backend.md)
- Готовите к проду? → [Модель безопасности](guides/security-model.md) и
  [Конкурентность](guides/concurrency.md)
