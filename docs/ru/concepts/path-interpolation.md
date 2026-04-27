# Интерполяция путей

Большинство сервисов работают на нескольких стейджах (dev / staging /
prod), нескольких регионах или для нескольких тенантов. Интерполяция
путей в vaultly делает один класс конфига пригодным для всего этого:

```python
class App(SecretModel):
    stage: str
    db_password: str = Secret("/{stage}/db/password")


prod = App(stage="prod", backend=...)  # читает /prod/db/password
dev  = App(stage="dev",  backend=...)  # читает /dev/db/password
```

## Как резолвится `{var}`

Плейсхолдеры используют [синтаксис `str.format`](https://docs.python.org/3/library/string.html#format-string-syntax).
На этапе фетча vaultly подставляет вместо них **скалярные не-секретные
поля** модели:

```python
class App(SecretModel):
    stage: str          # для {stage}
    region: str         # для {region}
    db_password: str = Secret("/{region}/{stage}/db/password")
```

При конструировании vaultly обходит все секретные пути, вытаскивает
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

## Вложенные модели используют контекст корня

Вложенные `SecretModel` не имеют собственного контекста — они всегда
резолвятся относительно **корня**. Это сделано намеренно: шаблоны путей
становятся предсказуемыми, и нет неоднозначности, когда одна и та же
`{var}` могла бы прийти с разных уровней.

```python
class DbConfig(SecretModel):
    password: str = Secret("/{stage}/db/password")
    pool_size: int = Secret("/{stage}/db/pool_size")
    # {stage} берётся из родительского `stage`, не из DbConfig

class App(SecretModel):
    stage: str
    db: DbConfig
```

`DbConfig`, созданный отдельно (без родительского контекста), откладывает
валидацию пути — модель может быть позже привязана к родителю. Если
такая standalone-модель так и не получит родителя, при первой попытке
фетча всплывёт `MissingContextVariableError`.

## Что можно подставлять

| Форма             | Поддержка   | Замечания                                     |
| ----------------- | ----------- | --------------------------------------------- |
| `{name}`          | да          | Стандартный случай.                           |
| `{{literal}}`     | да          | Экранированные скобки — пропускаются как `{literal}`. |
| `{0}` (positional)| нет         | Поднимает `MissingContextVariableError`.      |
| `{x.attr}`        | нет         | Тоже — мы не идём по атрибутам.               |
| `{x[0]}`          | нет         | Тоже — мы не индексируем.                     |

Все четыре последних случая ловятся при фетче с понятным
`MissingContextVariableError`, а не общим `KeyError` /
`AttributeError`.

## Когда два поля резолвятся в один путь

Результат `path.format(**context)` — это резолвленный путь, который
используется как ключ кэша. Если два разных поля дают один и тот же
резолвленный путь, они делят одну запись в кэше — и один фетч в
бэкенд.

Иногда это полезно (один секрет читается дважды, но обращение к
бэкенду одно), иногда удивляет (два `Secret(...)` для одного пути не
делают двойной фетч). Если сомневаетесь — давайте каждому полю свой
путь, или сверяйтесь со [справочником по Secret](secret-marker.md).
