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

vaultly работает с **KV v2**. KV v1 в v0.1 не поддерживается.

## Синтаксис `path:key`

Vault хранит каждый секрет как **dict пар key/value** на конкретном пути.
Один `Backend.get` возвращает одну строку, поэтому есть два способа
проецировать multi-field Vault-запись:

### Дефолтный ключ

Если ваш `path` не содержит `:`, vaultly читает `data[default_key]`,
где `default_key` — `"value"` по умолчанию:

```python
# Vault: secret/data/myapp/api_key  →  {"value": "sk-…"}
api_key: str = Secret("/myapp/api_key")     # читает поле "value"
```

### Per-field ключ через `:`

Если ваш `path` оканчивается на `:<keyname>`, vaultly читает указанное
поле:

```python
# Vault: secret/data/myapp/db   →   {"username": "admin", "password": "s3cr3t"}
db_user: str = Secret("/myapp/db:username")
db_pass: str = Secret("/myapp/db:password")
```

Это даёт две отдельных записи в кэше vaultly из одной Vault-записи.

## Mount point

По умолчанию `secret`. Override per backend:

```python
backend = VaultBackend(url=..., token=..., mount_point="my-kv")
```

Полный Vault-путь становится `my-kv/data/<your-path>`.

## Не-строковые значения

KV v2 хранит произвольный JSON, поэтому один секрет может быть dict, list,
int или bool. vaultly нормализует:

| Сохранённое значение в Vault | Что вернёт `Backend.get` |
| ---------------------------- | ------------------------ |
| `"hello"` (строка)           | `"hello"` (без изменений) |
| `42` (int)                   | `"42"`                   |
| `true` (bool)                | `"true"`                 |
| `{"k": "v"}` (dict)          | `'{"k": "v"}'` (валидный JSON) |
| `[1, 2, 3]` (list)           | `'[1, 2, 3]'` (валидный JSON) |

В связке с правилами каста vaultly это означает, что `dict`-поле в вашей
модели получит обратно `dict`, `int`-поле — `int` и т.д.

## Обновление токена

Статичные токены (`token=...`) подходят для долгоживущих service
account'ов. Для короткоживущих (AppRole, K8s auth, JWT) передайте
`token_factory`:

```python
def renew() -> str:
    # вызвать AppRole login / перечитать serviceaccount JWT / и т.д.
    return new_token

backend = VaultBackend(url=..., token=initial, token_factory=renew)
```

vaultly вызывает `token_factory()` ровно один раз на cold-cache fetch
при `Unauthorized`, ставит результат на hvac-клиент и повторяет чтение
один раз. Per-key fetch lock'и гарантируют, что 100 потоков, гонящихся
за одним истёкшим токеном, всё равно получат один renewal-вызов.

Если обновлённый токен тоже отвергнут — vaultly поднимает `AuthError`.
Если `token_factory` сам кинул исключение — оно пробрасывается как
`AuthError` с оригиналом в `__cause__`.

## Версионирование

KV v2 хранит каждую запись как новую версию. Закрепить конкретную:

```python
pinned: str = Secret("/myapp/api_key", version=2)
```

vaultly передаёт `version=2` в `read_secret_version(version=...)` от hvac.

## Маппинг ошибок

| hvac исключение                                | vaultly маппит в       |
| ---------------------------------------------- | ---------------------- |
| `InvalidPath`                                  | `SecretNotFoundError`  |
| `Forbidden`                                    | `AuthError`            |
| `Unauthorized`                                 | `AuthError` (после `token_factory`-ретрая, если есть) |
| `InternalServerError`                          | `TransientError`       |
| `requests.ConnectionError`, `requests.Timeout` | `TransientError`       |
| Другие подклассы `VaultError`                  | `TransientError`       |

## Связка с ретраями

```python
from vaultly import RetryingBackend
from vaultly.backends.vault import VaultBackend

backend = RetryingBackend(
    VaultBackend(url=..., token=..., token_factory=renew),
    max_attempts=3,
    total_timeout=10.0,
)
```

Token-renewal происходит **внутри** `VaultBackend`, до того как
`TransientError` дойдёт до retry-слоя. Так что `RetryingBackend`
ретраит только реальные сбои бэкенда, не auth-issues.

## Рецепт: K8s service-account auth

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
    token=k8s_login(),       # initial login на старте
    token_factory=k8s_login, # перелогин на Unauthorized
)
```
