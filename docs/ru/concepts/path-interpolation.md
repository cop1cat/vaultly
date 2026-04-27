# Интерполяция путей

Большинство сервисов работают с несколькими стейджами (dev / staging /
prod), тенантами или регионами и хотят один класс конфига для всех.
Интерполяция путей в vaultly делает это декларативно:

```python
class App(SecretModel):
    stage: str
    db_password: str = Secret("/{stage}/db/password")


prod = App(stage="prod", backend=...)  # читает /prod/db/password
dev  = App(stage="dev",  backend=...)  # читает /dev/db/password
```

## Как резолвится `{var}`

Плейсхолдеры используют [синтаксис `str.format`](https://docs.python.org/3/library/string.html#format-string-syntax).
На этапе фетча vaultly заполняет их **скалярными не-секретными полями**
модели:

```python
class App(SecretModel):
    stage: str          # для {stage}
    region: str         # для {region}
    db_password: str = Secret("/{region}/{stage}/db/password")
```

При конструировании vaultly обходит каждый секретный путь, извлекает
имена плейсхолдеров и проверяет, что каждое из них совпадает с
не-секретным полем **корневой** модели. Опечатка поднимает
`MissingContextVariableError` сразу:

```python
class Broken(SecretModel):
    stage: str
    db: str = Secret("/{stge}/db/password")   # опечатка


Broken(stage="prod", backend=...)
# > MissingContextVariableError: secret field Broken.db references {stge},
#   but no such field exists on the root model
```

## Вложенные модели делят контекст корня

Вложенные `SecretModel`-поля не имеют собственного контекста — они всегда
резолвятся относительно **корня**. Это by design: делает шаблоны путей
предсказуемыми и убирает неоднозначность, когда одна и та же `{var}`
могла бы прийти с разных уровней.

```python
class DbConfig(SecretModel):
    password: str = Secret("/{stage}/db/password")
    pool_size: int = Secret("/{stage}/db/pool_size")
    # {stage} резолвится относительно поля `stage` *родителя*

class App(SecretModel):
    stage: str
    db: DbConfig
```

`DbConfig`, созданный отдельно (без родительского контекста), откладывает
валидацию пути — модель может быть позже обёрнута в родителя. Standalone
`DbConfig`, который никогда не оборачивают, при первой попытке фетча
поднимет `MissingContextVariableError`.

## Допустимые плейсхолдеры

| Форма             | Поддержка   | Заметки                                       |
| ----------------- | ----------- | --------------------------------------------- |
| `{name}`          | да          | Стандартный случай.                           |
| `{{literal}}`     | да          | Экранированные скобки — пропускаются как `{literal}`. |
| `{0}` (positional) | нет        | Поднимает `MissingContextVariableError`.      |
| `{x.attr}`        | нет         | Тоже — мы не идём по атрибутам.               |
| `{x[0]}`          | нет         | Тоже — мы не индексируем.                     |

Все четыре edge-case'а ловятся на этапе фетча с понятным
`MissingContextVariableError`, а не общим `KeyError`/`AttributeError`.

## Когда значение содержит плейсхолдеры

Результат `path.format(**context)` — это резолвленный путь, используемый
как ключ кэша. Если два разных поля производят один резолвленный путь —
они делят одну запись кэша и один фетч в бэкенд.

Иногда это полезно (один секрет используется дважды, один вызов
бэкенда), иногда удивляет (`Secret(...)` объявленный дважды для одного
пути не делает двойной фетч). Если сомневаетесь — давайте каждому полю
свой путь или сверяйтесь со [справочником по Secret](secret-marker.md).
