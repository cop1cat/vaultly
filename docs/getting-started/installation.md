# Installation

vaultly requires **Python 3.12+** and **Pydantic 2.6+**.

## Core install

```sh
pip install vaultly
```

The core install ships `EnvBackend` (environment variables), `MockBackend`
(for tests), and `RetryingBackend` (the retry wrapper). No SDK dependencies.

## With cloud backends

Backends that talk to external services live behind extras so that
`pip install vaultly` doesn't pull boto3 / hvac unless you actually need them.

=== "AWS SSM Parameter Store"

    ```sh
    pip install 'vaultly[aws]'
    ```

    Pulls in `boto3`. Use:

    ```python
    from vaultly.backends.aws_ssm import AWSSSMBackend
    ```

=== "HashiCorp Vault"

    ```sh
    pip install 'vaultly[vault]'
    ```

    Pulls in `hvac`. Use:

    ```python
    from vaultly.backends.vault import VaultBackend
    ```

=== "Both"

    ```sh
    pip install 'vaultly[aws,vault]'
    ```

## With type checking

vaultly ships a `py.typed` marker, so `mypy` and `pyright` will pick up the
inline annotations automatically.

For `mypy`, enable the Pydantic plugin in `pyproject.toml`:

```toml
[tool.mypy]
plugins = ["pydantic.mypy"]
```

For `pyright`, no extra config is required — it understands Pydantic's
`@dataclass_transform` natively.

## Dev install

If you're contributing to vaultly itself:

```sh
git clone https://github.com/dspiridonov/vaultly
cd vaultly
uv sync --all-extras --group dev
uv run pytest
```
