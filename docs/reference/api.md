# API reference

Auto-generated from docstrings via [mkdocstrings](https://mkdocstrings.github.io/).

## Top-level

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

## Backends

::: vaultly.Backend
    options:
      show_bases: false

::: vaultly.EnvBackend

::: vaultly.MockBackend

::: vaultly.RetryingBackend

### Cloud backends

These require their respective extras (`pip install 'vaultly[aws]'` /
`'vaultly[vault]'`).

::: vaultly.backends.aws_ssm.AWSSSMBackend

::: vaultly.backends.vault.VaultBackend

## Errors

::: vaultly.VaultlyError

::: vaultly.ConfigError

::: vaultly.MissingContextVariableError

::: vaultly.SecretNotFoundError

::: vaultly.AuthError

::: vaultly.TransientError
