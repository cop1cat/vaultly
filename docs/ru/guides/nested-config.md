# Вложенные модели конфига

В реальных сервисах конфиг не плоский. Есть DB-секция, cache-секция,
секция API-ключей. vaultly поддерживает вложение `SecretModel`
напрямую.

```python
from vaultly import Secret, SecretModel


class DbConfig(SecretModel):
    password: str = Secret("/{stage}/db/password")
    pool_size: int = Secret("/{stage}/db/pool_size")


class CacheConfig(SecretModel):
    redis_url: str = Secret("/{stage}/cache/url")


class App(SecretModel):
    stage: str
    db: DbConfig
    cache: CacheConfig
    openai_key: str = Secret("/services/openai/key")
```

## Конструирование

Вложенных детей можно передавать как dict (стандартный паттерн
Pydantic):

```python
config = App(stage="prod", db={}, cache={}, backend=AWSSSMBackend(...))
```

Pydantic-валидатор примет dict и сконструирует `DbConfig` /
`CacheConfig`. После этого `model_validator(mode='after')` от vaultly
привяжет детей к корню.

Можно и предконструировать:

```python
config = App(stage="prod", db=DbConfig(), cache=CacheConfig(), backend=...)
```

Дитя, созданное так без родительского контекста, откладывает валидацию
пути — vaultly видит, что `DbConfig()` отдельно не имеет поля
`{stage}`, и оставляет резолюцию тому, кто его обернёт.

## Что вложенные дети получают от корня

При привязке к корню дитя начинает использовать три его вещи:

- **Бэкенд** — берётся только корневой `backend=`. Поле `backend` у
  ребёнка игнорируется после привязки.
- **Кэш** — `config.db.refresh("password")` чистит ту же запись, что
  и `config.refresh(...)` для того же пути.
- **Контекст пути** — `{stage}` в `DbConfig` резолвится из поля
  `stage` корня, а не из самого `DbConfig`.

## Refresh из любой точки дерева

`refresh` и `refresh_all` ходят по тому же общему кэшу, откуда бы их
ни вызвать:

```python
# Эквивалентно: оба чистят /prod/db/password.
config.db.refresh("password")
config.db._effective_root().refresh_all()  # очищает весь кэш
```

Большинство сервисов после ротации просто вызывают
`config.refresh_all()`.

## Path-валидация по всему дереву

`validate="paths"` обходит всё дерево при конструировании. Опечатка в
любом вложенном поле всплывает сразу, с указанием конкретного поля:

```python
class BadDb(SecretModel):
    password: str = Secret("/{stge}/password")   # опечатка

class App(SecretModel):
    stage: str
    db: BadDb

App(stage="prod", db={}, backend=...)
# > MissingContextVariableError: secret field BadDb.password references {stge},
#   but no such field exists on the root model
```

## Ограничения

- **Циклы** между моделями не поддерживаются (Pydantic откажется
  такое конструировать в любом случае).
- **Списки или dict из `SecretModel`** специально не поддерживаются —
  для vaultly это просто Pydantic-типы. Не кладите `list[SecretModel]`
  в модель в надежде, что секреты каждого элемента будут делить кэш
  родителя. Используйте одну `SecretModel` на один логический инстанс.
- **Не делайте `model_copy` на ребёнке**, чтобы переиспользовать его в
  другом дереве. Копирование заблокировано как раз потому, что кэш и
  связи `_root` стали бы неоднозначными. Создавайте свежий экземпляр.
