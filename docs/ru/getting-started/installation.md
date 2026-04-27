# Установка

vaultly требует **Python 3.12+** и **Pydantic 2.6+**.

## Базовая установка

```sh
pip install vaultly
```

Базовая установка содержит `EnvBackend` (переменные окружения),
`MockBackend` (для тестов) и `RetryingBackend` (обёртка с ретраями).
Никаких зависимостей от облачных SDK.

## С облачными бэкендами

Бэкенды, которые работают с внешними сервисами, лежат за extras —
`pip install vaultly` не тянет boto3 / hvac, пока они вам не нужны.

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

## С проверкой типов

vaultly содержит маркер `py.typed`, поэтому `mypy` и `pyright`
автоматически подхватывают встроенные аннотации.

Для `mypy` включите Pydantic-плагин в `pyproject.toml`:

```toml
[tool.mypy]
plugins = ["pydantic.mypy"]
```

Для `pyright` дополнительная настройка не нужна — он понимает Pydantic
`@dataclass_transform` нативно.

## Dev-установка

Если вы контрибьютите в саму vaultly:

```sh
git clone https://github.com/cop1cat/vaultly
cd vaultly
uv sync --all-extras --group dev
uv run pytest
```
