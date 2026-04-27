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

import json
import threading
import time
from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from collections.abc import Callable

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
        token_factory: Callable[[], str] | None = None,
        reuse_connection: bool = True,
        idle_timeout: float | None = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Vault backend.

        Args:
            url: Vault server URL.
            token: Static auth token.
            mount_point: KV v2 mount point (default `"secret"`).
            default_key: Field within the secret dict to return when the
                path doesn't carry a `:key` suffix.
            client: Pass a pre-configured `hvac.Client` instead of letting
                us create one. When given, `reuse_connection`,
                `idle_timeout`, and `client_kwargs` are ignored — you own
                the client's lifecycle.
            token_factory: Optional callable invoked when the current token
                is rejected (`Unauthorized`). Should return a fresh token;
                we install it on the client and retry the read once.
                Use this with short-lived tokens (AppRole, K8s auth, etc.).
            reuse_connection: When `True` (default), one persistent
                `hvac.Client` (and its underlying `requests.Session`) is
                kept for the backend's lifetime. Set `False` for a fresh
                client per call — useful when reads are infrequent and
                idle TCP connections get dropped by an LB / proxy.
            idle_timeout: Recreate the persistent client when the gap
                between calls exceeds this many seconds. Compromise
                between `reuse_connection=True` (cheap, may stale) and
                `reuse_connection=False` (always fresh, slow). Ignored
                if `reuse_connection=False`.
            client_kwargs: Extra kwargs forwarded to `hvac.Client(...)`
                (e.g. `verify=False`, custom `adapter`). Used when we
                construct or recreate the client; ignored when `client=`
                was passed.
        """
        self.mount_point = mount_point
        self.default_key = default_key
        self._token_factory = token_factory

        # Connection management.
        self._user_client = client
        self._url = url
        self._token = token
        self._reuse = reuse_connection
        self._idle_timeout = idle_timeout
        self._client_kwargs = dict(client_kwargs) if client_kwargs else {}

        # Set on first use; protected by `_client_lock` for thread safety.
        self._client_lock = threading.Lock()
        self._cached_client: Any = client  # may stay None until first call
        self._cached_at: float | None = None

    def _make_client(self) -> Any:
        return hvac.Client(url=self._url, token=self._token, **self._client_kwargs)

    def _client(self) -> Any:
        # The user-supplied client is always returned as-is — we don't manage
        # its lifecycle.
        if self._user_client is not None:
            return self._user_client

        if not self._reuse:
            # Fresh client per call. Caller pays for a new TCP connection
            # and TLS handshake every read; useful for very-rare reads.
            return self._make_client()

        with self._client_lock:
            now = time.monotonic()
            stale = (
                self._idle_timeout is not None
                and self._cached_at is not None
                and (now - self._cached_at) > self._idle_timeout
            )
            if self._cached_client is None or stale:
                self._cached_client = self._make_client()
            self._cached_at = now
            return self._cached_client

    def get(self, path: str, *, version: int | str | None = None) -> str:
        secret_path, key = self._split(path)
        try:
            resp = self._read(secret_path, version)
        except hvac_exc.Unauthorized as e:
            if self._token_factory is None:
                msg = (
                    f"Vault token is invalid or expired (reading {secret_path})"
                )
                raise AuthError(msg) from e
            try:
                new_token = self._token_factory()
            except Exception as factory_err:
                msg = (
                    f"token_factory raised while renewing Vault token "
                    f"(reading {secret_path}): {factory_err}"
                )
                raise AuthError(msg) from factory_err
            # Persist for any future _make_client() (relevant when
            # reuse_connection=False) and update the live client.
            self._token = new_token
            current = self._cached_client if self._user_client is None else self._user_client
            if current is not None:
                current.token = new_token
            try:
                resp = self._read(secret_path, version)
            except hvac_exc.Unauthorized as e2:
                msg = (
                    f"Vault still rejects renewed token (reading "
                    f"{secret_path})"
                )
                raise AuthError(msg) from e2

        data = resp.get("data", {}).get("data", {})
        if key not in data:
            msg = (
                f"Vault path {secret_path!r} has no key {key!r} "
                f"(found keys: {sorted(data)})"
            )
            raise SecretNotFoundError(msg)
        return _normalize(data[key])

    def _read(self, secret_path: str, version: int | str | None) -> dict[str, Any]:
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
            return self._client().secrets.kv.v2.read_secret_version(**kw)
        except hvac_exc.InvalidPath as e:
            msg = f"Vault path not found: {secret_path}"
            raise SecretNotFoundError(msg) from e
        except hvac_exc.Forbidden as e:
            msg = f"Vault access denied for {secret_path}"
            raise AuthError(msg) from e
        except hvac_exc.Unauthorized:
            raise  # caller decides whether to retry via token_factory
        except hvac_exc.InternalServerError as e:
            msg = f"Vault server error reading {secret_path}: {e}"
            raise TransientError(msg) from e
        except (RequestsConnectionError, RequestsTimeout) as e:
            msg = f"Vault connection error reading {secret_path}: {e}"
            raise TransientError(msg) from e
        except hvac_exc.VaultError as e:
            self._raise_unexpected(e, secret_path)

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


def _normalize(val: Any) -> str:
    """Project Vault's JSON-typed value down to the string our cast layer wants.

    Vault's KV v2 stores arbitrary JSON, so a single secret entry can be a
    dict, list, int, bool, etc. — not just a string. `Backend.get` must
    return `str`, but `cast_value` will then `json.loads` for `dict` / `list`
    fields and `int(...)` for numeric fields. So:
      * `str`  -> return as-is (so that `cast_value` for `str` round-trips
                  cleanly without picking up extra JSON quotes).
      * other  -> `json.dumps` so dicts and lists stay valid JSON, numbers
                  and bools serialize to their textual form.
    """
    if isinstance(val, str):
        return val
    return json.dumps(val)
