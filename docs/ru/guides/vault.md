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

vaultly работает с **KV v2**. KV v1 не поддерживается.

## Синтаксис `path:key`

Vault хранит каждый секрет как **dict пар key/value** на конкретном
пути. Один `Backend.get` возвращает одну строку — поэтому есть два
способа спроецировать multi-field Vault-запись:

### Дефолтный ключ

Если в `path` нет `:`, vaultly читает `data[default_key]`, где
`default_key` по умолчанию — `"value"`:

```python
# Vault: secret/data/myapp/api_key  →  {"value": "sk-…"}
api_key: str = Secret("/myapp/api_key")     # читает поле "value"
```

### Конкретный ключ через `:`

Если `path` оканчивается на `:<keyname>`, vaultly читает указанное
поле:

```python
# Vault: secret/data/myapp/db   →   {"username": "admin", "password": "s3cr3t"}
db_user: str = Secret("/myapp/db:username")
db_pass: str = Secret("/myapp/db:password")
```

Получаются две отдельные записи в кэше из одной Vault-записи.

## Mount point

По умолчанию `secret`. Override через параметр:

```python
backend = VaultBackend(url=..., token=..., mount_point="my-kv")
```

Полный путь в Vault становится `my-kv/data/<your-path>`.

## Не-строковые значения

KV v2 хранит произвольный JSON, поэтому один секрет может быть dict,
list, int или bool. vaultly нормализует:

| Сохранено в Vault           | Что вернёт `Backend.get`        |
| --------------------------- | ------------------------------- |
| `"hello"` (строка)          | `"hello"` (без изменений)        |
| `42` (int)                  | `"42"`                          |
| `true` (bool)               | `"true"`                        |
| `{"k": "v"}` (dict)         | `'{"k": "v"}'` (валидный JSON)  |
| `[1, 2, 3]` (list)          | `'[1, 2, 3]'` (валидный JSON)   |

Вместе с правилами приведения типов это даёт ожидаемое: `dict`-поле в
модели получит обратно `dict`, `int`-поле — `int` и так далее.

## Управление токеном

Статичные токены (`token=...`) подходят для долгоживущих сервисных
аккаунтов. Для коротких токенов (AppRole, K8s auth, JWT) передайте
`token_factory`:

```python
def renew() -> str:
    # AppRole login / перечитать serviceaccount JWT / ...
    return new_token

backend = VaultBackend(url=..., token=initial, token_factory=renew)
```

vaultly вызывает `token_factory()` ровно один раз на cold-cache-фетч
при `Unauthorized`, ставит результат на hvac-клиент и повторяет
чтение. Per-key fetch lock'и гарантируют, что 100 потоков на одном
истёкшем токене дадут один renewal-вызов, а не 100.

Если обновлённый токен тоже отвергнут — vaultly поднимает `AuthError`.
Если `token_factory` сам кинул исключение — оно прокидывается как
`AuthError` с оригиналом в `__cause__`.

## Управление соединением

По умолчанию `VaultBackend` держит один долгоживущий `hvac.Client`
(и `requests.Session` под ним) на всё время своей жизни. Для часто
читающих сервисов это правильно — TLS-handshake амортизируется.

Для **редких чтений** (раз в час) idle TCP-соединение через NLB / ELB /
прокси может быть закрыто. Тогда первый запрос после простоя упадёт с
сетевой ошибкой (vaultly видит её как `TransientError`, retry-слой
поможет — но это лишний шум). Два варианта:

```python
# Вариант 1: пересоздавать клиент, если между вызовами прошло > 5 минут.
backend = VaultBackend(url=..., token=..., idle_timeout=300.0)

# Вариант 2: создавать новый клиент на каждый запрос. Дороже по latency,
# но никогда не споткнётся об dead-сокет.
backend = VaultBackend(url=..., token=..., reuse_connection=False)
```

Дополнительные kwargs можно передать в hvac-клиент через
`client_kwargs=`:

```python
backend = VaultBackend(
    url="https://vault.example.com",
    token="...",
    client_kwargs={"verify": "/etc/ca/vault-ca.pem"},
)
```

Если вы передали свой `client=...`, эти три параметра игнорируются —
вы сами управляете жизненным циклом клиента.

## Версионирование

KV v2 хранит каждую запись как новую версию. Закрепить конкретную:

```python
pinned: str = Secret("/myapp/api_key", version=2)
```

vaultly передаёт `version=2` в `read_secret_version(version=...)` от
hvac.

## Маппинг ошибок

| hvac исключение                                | vaultly →              |
| ---------------------------------------------- | ---------------------- |
| `InvalidPath`                                  | `SecretNotFoundError`  |
| `Forbidden`                                    | `AuthError`            |
| `Unauthorized`                                 | `AuthError` (после `token_factory`-ретрая, если есть) |
| `InternalServerError`                          | `TransientError`       |
| `requests.ConnectionError`, `requests.Timeout` | `TransientError`       |
| Другие `VaultError`-подклассы                  | `TransientError`       |

## В связке с ретраями

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
`TransientError` дойдёт до retry-слоя. Поэтому `RetryingBackend`
ретраит только реальные сбои бэкенда, не auth.

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
    token=k8s_login(),       # начальный логин
    token_factory=k8s_login, # перелогин на Unauthorized
)
```
