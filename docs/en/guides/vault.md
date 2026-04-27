# HashiCorp Vault

```sh
pip install 'vaultly[vault]'
```

```python
from vaultly.backends.vault import VaultBackend

backend = VaultBackend(
    url="https://vault.example.com",
    token=os.environ["VAULT_TOKEN"],
)
```

vaultly speaks **KV v2**. KV v1 is not supported.

## `path:key` syntax

Vault stores each secret as a **dict of key/value pairs** at a given path.
A single `Backend.get` returns one string, so we have two ways to project a
multi-field Vault entry:

### Default key

If your `path` doesn't contain `:`, vaultly reads `data[default_key]`,
where `default_key` is `"value"` by default:

```python
# Vault: secret/data/myapp/api_key  →  {"value": "sk-…"}
api_key: str = Secret("/myapp/api_key")     # reads the "value" field
```

### Per-field key via `:`

If your `path` ends with `:<keyname>`, vaultly reads that specific field:

```python
# Vault: secret/data/myapp/db   →   {"username": "admin", "password": "s3cr3t"}
db_user: str = Secret("/myapp/db:username")
db_pass: str = Secret("/myapp/db:password")
```

This pulls two separate vaultly cache entries from one Vault entry.

## Mount point

Default is `secret`. Override per backend instance:

```python
backend = VaultBackend(url=..., token=..., mount_point="my-kv")
```

The full Vault path becomes `my-kv/data/<your-path>`.

## Non-string values

Vault KV v2 stores arbitrary JSON, so a single secret can be a dict, list,
int, or bool. vaultly normalizes:

| Vault stored value     | What `Backend.get` returns |
| ---------------------- | -------------------------- |
| `"hello"` (string)     | `"hello"` (unchanged)      |
| `42` (int)             | `"42"`                     |
| `true` (bool)          | `"true"`                   |
| `{"k": "v"}` (dict)    | `'{"k": "v"}'` (valid JSON)|
| `[1, 2, 3]` (list)     | `'[1, 2, 3]'` (valid JSON) |

Combined with vaultly's cast rules, this means a `dict` field declared
in your model gets back a `dict`, an `int` field gets back an `int`, etc.

## Token renewal

Static tokens (`token=...`) are fine for long-lived service accounts. For
short-lived tokens (AppRole, K8s auth, JWT), pass a `token_factory`:

```python
def renew() -> str:
    # call AppRole login / re-read serviceaccount JWT / etc.
    return new_token

backend = VaultBackend(url=..., token=initial, token_factory=renew)
```

vaultly invokes `token_factory()` exactly once per cold-cache fetch on
`Unauthorized`, installs the result on the hvac client, and retries the
read once. Per-key fetch locks ensure that 100 threads racing on an
expired token still produce a single renewal call.

If the renewed token is also rejected, vaultly raises `AuthError`. If
`token_factory` itself raises, that surfaces as `AuthError` with the
factory's exception preserved as `__cause__`.

## Connection management

By default `VaultBackend` keeps a single long-lived `hvac.Client` (and
its underlying `requests.Session`) for its entire lifetime. For
frequently-reading services this is the right call — the TLS handshake
amortizes across reads.

For **infrequent reads** (once per hour) an idle TCP connection through
an NLB / ELB / proxy may get dropped. The first request after the gap
then fails with a network error (vaultly surfaces it as `TransientError`,
so the retry layer recovers — but it's noise). Two ways to avoid that:

```python
# Option 1: recreate the client when the gap between calls exceeds 5 min.
backend = VaultBackend(url=..., token=..., idle_timeout=300.0)

# Option 2: a fresh client per call. Costlier per-read latency, but
# never trips on a dead socket.
backend = VaultBackend(url=..., token=..., reuse_connection=False)
```

Extra kwargs can be forwarded to the hvac client via `client_kwargs=`:

```python
backend = VaultBackend(
    url="https://vault.example.com",
    token="...",
    client_kwargs={"verify": "/etc/ca/vault-ca.pem"},
)
```

If you pass your own `client=...`, these three knobs are ignored — you
own the client's lifecycle.

## Versioning

KV v2 stores every write as a new version. Pin a specific one:

```python
pinned: str = Secret("/myapp/api_key", version=2)
```

vaultly forwards `version=2` to hvac's `read_secret_version(version=...)`.

## Error mapping

| hvac exception                                | vaultly maps to        |
| --------------------------------------------- | ---------------------- |
| `InvalidPath`                                 | `SecretNotFoundError`  |
| `Forbidden`                                   | `AuthError`            |
| `Unauthorized`                                | `AuthError` (after `token_factory` retry, if any) |
| `InternalServerError`                         | `TransientError`       |
| `requests.ConnectionError`, `requests.Timeout`| `TransientError`       |
| Other `VaultError` subclasses                 | `TransientError`       |

## Combining with retries

```python
from vaultly import RetryingBackend
from vaultly.backends.vault import VaultBackend

backend = RetryingBackend(
    VaultBackend(url=..., token=..., token_factory=renew),
    max_attempts=3,
    total_timeout=10.0,
)
```

Token renewal happens **inside** `VaultBackend`, before `TransientError`
ever reaches the retry layer. So `RetryingBackend` only retries actual
backend flakes, not auth issues.

## Recipe: K8s service-account auth

```python
import pathlib
import hvac

def k8s_login() -> str:
    jwt = pathlib.Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text()
    client = hvac.Client(url="https://vault.example.com")
    resp = client.auth.kubernetes.login(role="my-app", jwt=jwt)
    return resp["auth"]["client_token"]


backend = VaultBackend(
    url="https://vault.example.com",
    token=k8s_login(),       # initial login at boot
    token_factory=k8s_login, # re-login on Unauthorized
)
```
