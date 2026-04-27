# AWS SSM Parameter Store

```sh
pip install 'vaultly[aws]'
```

```python
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = AWSSSMBackend(region_name="eu-west-1")
```

## What it does

- Reads parameters via `boto3.client("ssm").get_parameter(...)` for single
  fetches and `get_parameters(...)` for batches (capped at 10 names per
  call by SSM; `vaultly` chunks larger requests automatically).
- Decrypts `SecureString` parameters by passing `WithDecryption=True`
  (default; pass `with_decryption=False` to disable).
- Maps boto3 / botocore exceptions to vaultly's error hierarchy.

## Default config

`AWSSSMBackend(region_name="...")` constructs a `boto3` SSM client with a
sensible default `botocore.Config`:

```python
DEFAULT_CONFIG = Config(
    retries={"mode": "adaptive", "max_attempts": 3},
    connect_timeout=2.0,
    read_timeout=5.0,
)
```

This protects you from indefinite hangs on a network blip. Override:

```python
from botocore.config import Config

backend = AWSSSMBackend(
    region_name="eu-west-1",
    config=Config(read_timeout=10.0),
)
```

Or pass a fully-configured client:

```python
import boto3

client = boto3.client("ssm", config=my_org_config)
backend = AWSSSMBackend(client=client)
```

## Versioning

SSM stores every change as a new version. Pin a specific one:

```python
class App(SecretModel):
    pinned: str = Secret("/db/password", version=2)
    latest: str = Secret("/db/password")
```

`vaultly` translates `version=2` to SSM's `Name=/db/password:2` syntax.

## IAM

The IAM identity running your app needs `ssm:GetParameter` for single
reads and `ssm:GetParameters` for batched reads. If your secrets are
`SecureString` (recommended), also grant `kms:Decrypt` on the relevant
KMS key.

A minimal least-privilege policy for `prod`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters"],
      "Resource": "arn:aws:ssm:eu-west-1:123:parameter/prod/*"
    },
    {
      "Effect": "Allow",
      "Action": ["kms:Decrypt"],
      "Resource": "arn:aws:kms:eu-west-1:123:key/<key-id>"
    }
  ]
}
```

## Error mapping

| boto3 / SSM error                                                 | vaultly maps to        |
| ----------------------------------------------------------------- | ---------------------- |
| `ParameterNotFound`                                               | `SecretNotFoundError`  |
| `AccessDeniedException`, `UnauthorizedAccessException`            | `AuthError`            |
| `ThrottlingException`, `RequestLimitExceeded`, `ServiceUnavailable`, `InternalServerError` | `TransientError` |
| `BotoCoreError` (network-level)                                   | `TransientError`       |
| Anything else (unknown `ClientError` codes)                       | `TransientError` — let `RetryingBackend` decide |

## Combining with retries

```python
from vaultly import RetryingBackend
from vaultly.backends.aws_ssm import AWSSSMBackend

backend = RetryingBackend(
    AWSSSMBackend(region_name="eu-west-1"),
    max_attempts=3,
    total_timeout=10.0,
)
```

Note that boto3 already retries at the transport layer (configured via
`Config.retries`). `RetryingBackend` is a *semantic* layer on top — it
retries vaultly's `TransientError`, which only fires after boto3 has
already given up. Don't increase both budgets to "many" or you'll
multiply.

## Recipes

### Hierarchical, multi-stage app

```python
class DbConfig(SecretModel):
    password: str = Secret("/{stage}/db/password")
    pool_size: int = Secret("/{stage}/db/pool")

class App(SecretModel, validate="fetch"):
    stage: str
    db: DbConfig
    api_key: str = Secret("/{stage}/api/key")
    flags: dict = Secret("/{stage}/feature_flags")

config = App(stage="prod", db={}, backend=AWSSSMBackend(region_name="eu-west-1"))
```

`validate="fetch"` prefetches everything via one batched `GetParameters`
call (or two if you have more than 10 secrets), so a missing or
mis-permissioned parameter fails the deploy at startup rather than at
3 a.m. when someone reads it.

### Local + prod with the same model

```python
import os
from vaultly import EnvBackend
from vaultly.backends.aws_ssm import AWSSSMBackend


def make_backend():
    if os.getenv("ENV") == "local":
        return EnvBackend(prefix="MYAPP")
    return AWSSSMBackend(region_name=os.environ["AWS_REGION"])


config = App(stage=os.environ["STAGE"], backend=make_backend())
```
