# Справочник API

Автоматически сгенерирован из docstring'ов через
[mkdocstrings](https://mkdocstrings.github.io/). Сами docstring'и
написаны на английском.

## Верхний уровень

::: vaultly
    options:
      show_root_heading: false
      members_order: source

## `vaultly.SecretModel`

::: vaultly.core.model.SecretModel
    options:
      show_bases: true
      members:
        - prefetch
        - refresh
        - refresh_all

## `vaultly.Secret`

::: vaultly.core.secret.Secret

## Бэкенды

::: vaultly.Backend
    options:
      show_bases: false

::: vaultly.EnvBackend

::: vaultly.MockBackend

::: vaultly.RetryingBackend

### Облачные бэкенды

Требуют соответствующих extras (`pip install 'vaultly[aws]'` /
`'vaultly[vault]'`).

::: vaultly.backends.aws_ssm.AWSSSMBackend

::: vaultly.backends.vault.VaultBackend

## Ошибки

::: vaultly.VaultlyError

::: vaultly.ConfigError

::: vaultly.MissingContextVariableError

::: vaultly.SecretNotFoundError

::: vaultly.AuthError

::: vaultly.TransientError
