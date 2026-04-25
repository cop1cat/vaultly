"""HashiCorp Vault backend (KV v2).

Install via the `[vault]` extra:

    pip install vaultly[vault]

Vault stores each secret as a dict of key/value pairs at a given path. We
need to project that down to a single string per `Backend.get` call. Two
options are supported:

  * Path with no `:` — read the dict at `path`, return `data[default_key]`.
  * Path containing `:` — split into `<path>:<key>`, read the dict, return
    `data[key]`.

Examples (with `mount_point="secret"`, `default_key="value"`):

    "myapp/db/password"        -> reads secret/data/myapp/db/password,
                                  returns the "value" field
    "myapp/db/creds:password"  -> reads secret/data/myapp/db/creds,
                                  returns the "password" field

Leading slashes are stripped — both `/db/password` and `db/password` work.

Tests mock `hvac.Client`. A real-cluster integration suite (docker dev
mode) can be added later; the public surface here matches what KV v2 with
real Vault behaves like.
"""

from __future__ import annotations

from typing import Any, NoReturn

try:
    import hvac
    import hvac.exceptions as hvac_exc
    from requests.exceptions import ConnectionError as RequestsConnectionError
    from requests.exceptions import Timeout as RequestsTimeout
except ImportError as e:  # pragma: no cover
    msg = (
        "VaultBackend requires hvac. Install the optional dependency:\n"
        "    pip install 'vaultly[vault]'"
    )
    raise ImportError(msg) from e

from vaultly.backends.base import Backend
from vaultly.errors import AuthError, SecretNotFoundError, TransientError


class VaultBackend(Backend):
    def __init__(
        self,
        *,
        url: str | None = None,
        token: str | None = None,
        mount_point: str = "secret",
        default_key: str = "value",
        client: Any = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            self._client = hvac.Client(url=url, token=token)
        self.mount_point = mount_point
        self.default_key = default_key

    def get(self, path: str, *, version: int | str | None = None) -> str:
        secret_path, key = self._split(path)
        # hvac expects an int for KV v2 versions; accept str at the API
        # surface and coerce so users can read versions from env vars etc.
        kw: dict[str, Any] = {
            "path": secret_path,
            "mount_point": self.mount_point,
            "raise_on_deleted_version": True,
        }
        if version is not None:
            kw["version"] = int(version)
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(**kw)
        except hvac_exc.InvalidPath as e:
            msg = f"Vault path not found: {secret_path}"
            raise SecretNotFoundError(msg) from e
        except hvac_exc.Forbidden as e:
            msg = f"Vault access denied for {secret_path}"
            raise AuthError(msg) from e
        except hvac_exc.Unauthorized as e:
            msg = f"Vault token is invalid or expired (reading {secret_path})"
            raise AuthError(msg) from e
        except hvac_exc.InternalServerError as e:
            msg = f"Vault server error reading {secret_path}: {e}"
            raise TransientError(msg) from e
        except (RequestsConnectionError, RequestsTimeout) as e:
            msg = f"Vault connection error reading {secret_path}: {e}"
            raise TransientError(msg) from e
        except hvac_exc.VaultError as e:
            self._raise_unexpected(e, secret_path)

        data = resp.get("data", {}).get("data", {})
        if key not in data:
            msg = (
                f"Vault path {secret_path!r} has no key {key!r} "
                f"(found keys: {sorted(data)})"
            )
            raise SecretNotFoundError(msg)
        return str(data[key])

    @staticmethod
    def _raise_unexpected(e: hvac_exc.VaultError, secret_path: str) -> NoReturn:
        # Anything we don't explicitly classify gets surfaced as transient
        # so RetryingBackend can decide; this avoids silently masking real
        # outages as misconfiguration.
        msg = f"Vault error reading {secret_path}: {type(e).__name__}: {e}"
        raise TransientError(msg) from e

    def _split(self, path: str) -> tuple[str, str]:
        path = path.lstrip("/")
        if ":" in path:
            secret_path, _, key = path.rpartition(":")
            return secret_path, key
        return path, self.default_key
