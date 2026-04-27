# Установка

Требуется **Python 3.12+** и **Pydantic 2.6+**.

## Базовая установка

```sh
pip install vaultly
```

В базовой установке есть `EnvBackend` (переменные окружения),
`MockBackend` (для тестов) и `RetryingBackend` (обёртка с ретраями).
SDK конкретных провайдеров не подтягиваются.

## С облачными бэкендами

Бэкенды для внешних сервисов вынесены в extras, чтобы `pip install
vaultly` не тянул boto3 / hvac, если они не нужны.

=== "AWS SSM Parameter Store"

    ```sh
    pip install 'vaultly[aws]'
    ```

    Подтянет `boto3`. Использование:

    ```python
    from vaultly.backends.aws_ssm import AWSSSMBackend
    ```

=== "HashiCorp Vault"

    ```sh
    pip install 'vaultly[vault]'
    ```

    Подтянет `hvac`. Использование:

    ```python
    from vaultly.backends.vault import VaultBackend
    ```

=== "Оба"

    ```sh
    pip install 'vaultly[aws,vault]'
    ```

## Проверка типов

В пакете лежит маркер `py.typed`, поэтому `mypy` и `pyright`
автоматически читают встроенные аннотации.

Для `mypy` включите Pydantic-плагин в `pyproject.toml`:

```toml
[tool.mypy]
plugins = ["pydantic.mypy"]
```

Для `pyright` ничего настраивать не нужно — он понимает Pydantic
`@dataclass_transform` нативно.

## Установка для разработки

Если контрибьютите в саму vaultly:

```sh
git clone https://github.com/cop1cat/vaultly
cd vaultly
uv sync --all-extras --group dev
uv run pytest
```
