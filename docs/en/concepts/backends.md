# Backends

A backend is the thing that actually fetches a secret. vaultly ships
several and lets you write your own.

## The `Backend` abstract base

```python
from abc import ABC, abstractmethod


class Backend(ABC):
    @abstractmethod
    def get(self, path: str, *, version: int | str | None = None) -> str:
        """Return the raw string at `path` or raise a vaultly error."""

    def get_batch(self, paths: list[str]) -> dict[str, str]:
        """Fetch many paths at once. Default: serial `get`. Dedupes inputs."""
```

Two methods, both returning strings. vaultly's `SecretModel` casts those
strings to the field's annotated type after fetch. Backends are NOT
responsible for type conversion.

`get_batch` is what `prefetch()` uses. The default implementation issues
serial `get` calls. Backends with a real batch API (SSM `GetParameters`,
Vault list, …) override it for efficiency.

## Built-in backends

| Backend           | Source / SDK     | Use case                                       |
| ----------------- | ---------------- | ---------------------------------------------- |
| `EnvBackend`      | `os.environ`     | Local dev, simple deployments, container envs. |
| `MockBackend`     | in-memory dict   | Tests. Tracks calls for assertions.            |
| `AWSSSMBackend`   | `boto3` SSM      | AWS Systems Manager Parameter Store.           |
| `VaultBackend`    | `hvac` KV v2     | HashiCorp Vault.                               |
| `RetryingBackend` | wraps any other  | Adds exponential-backoff retries on `TransientError`. |

The cloud backends require optional installs: `pip install 'vaultly[aws]'`
or `pip install 'vaultly[vault]'`.

## Picking one

See [Choosing a backend](../guides/choosing-a-backend.md) for the
decision tree. As a rough guide:

- **Local development** → `EnvBackend`
- **Tests** → `MockBackend`
- **AWS service** → `AWSSSMBackend` (likely wrapped in `RetryingBackend`)
- **Vault-backed shop** → `VaultBackend(token_factory=...)` (likely
  wrapped in `RetryingBackend`)

## Errors

Every built-in backend maps its underlying SDK exceptions to one of:

- `SecretNotFoundError` — the key/path doesn't exist. Not retried.
- `AuthError` — invalid credentials. Not retried.
- `TransientError` — timeout, throttling, 5xx. Retried by `RetryingBackend`.

If you write a custom backend, follow the same convention. Code in your
service's hot path catches `vaultly.VaultlyError` (the umbrella base) and
the more specific subclasses as needed.

See [Errors](errors.md) for the full hierarchy.

## Writing your own backend

Subclass `Backend` and implement `get`. Override `get_batch` if your
backend has a real bulk-fetch API.

```python
from vaultly import Backend
from vaultly.errors import SecretNotFoundError, TransientError, AuthError


class MyBackend(Backend):
    def __init__(self, my_client):
        self._client = my_client

    def get(self, path: str, *, version: int | str | None = None) -> str:
        try:
            return self._client.read(path, version=version)
        except MyClientNotFound as e:
            raise SecretNotFoundError(f"missing: {path}") from e
        except MyClientForbidden as e:
            raise AuthError(f"denied: {path}") from e
        except (MyClientTimeout, MyClient5xx) as e:
            raise TransientError(f"transient: {path}: {e}") from e
```

Then plug it into a `SecretModel` like any built-in:

```python
config = AppConfig(stage="prod", backend=MyBackend(client))
```

vaultly's caching, retries, and masking apply transparently.
