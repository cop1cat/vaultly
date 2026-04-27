# Вложенные модели конфига

Реальные сервисы не имеют одного плоского конфига. Они имеют DB-секцию,
cache-секцию, секцию API-ключей и т.д. vaultly поддерживает вложение
`SecretModel` нативно.

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

Создавайте вложенных детей inline как dict (стандартный паттерн
Pydantic):

```python
config = App(stage="prod", db={}, cache={}, backend=AWSSSMBackend(...))
```

Dict проходит валидатор Pydantic и производит инстанс
`DbConfig` / `CacheConfig`. `model_validator(mode='after')` от vaultly
затем подвязывает детей к корню.

Можно и пред-конструировать детей:

```python
config = App(stage="prod", db=DbConfig(), cache=CacheConfig(), backend=...)
```

Дитя, созданное так без родительского контекста, откладывает
path-валидацию — vaultly понимает, что `DbConfig()` standalone не имеет
поля `{stage}`, и оставляет резолюцию тому, кто его обернёт.

## Что вложенные дети делят с корнем

Дитя, подвязанное к корню, заимствует у него три вещи:

- **Бэкенд** — используется только `backend=` корня; поле `backend` детей
  игнорируется после wiring.
- **Кэш** — `config.db.refresh("password")` инвалидирует тот же слот,
  что и `config.refresh(...)`.
- **Контекст пути** — `{stage}` в `DbConfig` резолвится относительно
  поля `stage` корня, не самого `DbConfig`.

## Refresh из любой точки дерева

`refresh` и `refresh_all` ходят по тому же общему кэшу независимо от
того, откуда на дереве их вызвать:

```python
# Эквивалентно: оба инвалидируют /prod/db/password.
config.db.refresh("password")
config.db._effective_root().refresh_all()  # обнуляет всё
```

Большинство приложений просто вызывают `config.refresh_all()` после
деплоя / события ротации.

## Path-валидация по всему дереву

`validate="paths"` обходит всё дерево при конструировании. Опечатка в
любом вложенном поле всплывает сразу с путём до проблемного поля в
сообщении ошибки:

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
  конструировать такое в любом случае).
- **Списки / dict из `SecretModel`** специально не поддерживаются — с
  точки зрения vaultly это обычные Pydantic-типы. Не кладите
  `list[SecretModel]` в модель в надежде, что секреты каждого элемента
  будут делить кэш родителя. Используйте одну форму `SecretModel` на
  один логический инстанс.
- **Не делайте `model_copy` ребёнка** для переиспользования в другом
  дереве. Копирование заблокировано именно потому, что кэш и `_root`
  связи стали бы неоднозначными. Создавайте свежий.
