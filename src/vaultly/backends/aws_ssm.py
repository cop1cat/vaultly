"""AWS Systems Manager Parameter Store backend.

Uses boto3 under the hood. Install via the `[aws]` extra:

    pip install vaultly[aws]

`get_parameters` accepts up to 10 names per call, so `get_batch` chunks
larger requests. SDK-level retries (network, throttling) are left to boto3
config; semantic retries on `TransientError` are wired separately via
`RetryingBackend`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError as e:  # pragma: no cover
    msg = (
        "AWSSSMBackend requires boto3. Install the optional dependency:\n"
        "    pip install 'vaultly[aws]'"
    )
    raise ImportError(msg) from e

from vaultly.backends.base import Backend
from vaultly.errors import AuthError, SecretNotFoundError, TransientError

if TYPE_CHECKING:
    from collections.abc import Iterator

# AWS error codes — grouped by how we want them surfaced.
_AUTH_CODES = frozenset(
    {
        "AccessDeniedException",
        "UnauthorizedAccessException",
        "UnauthorizedOperation",
        "InvalidKeyId",
    }
)

_TRANSIENT_CODES = frozenset(
    {
        "ThrottlingException",
        "Throttling",
        "RequestLimitExceeded",
        "TooManyRequestsException",
        "ServiceUnavailable",
        "InternalServerError",
        "InternalFailure",
    }
)

_BATCH_LIMIT = 10  # SSM GetParameters hard limit


class AWSSSMBackend(Backend):
    def __init__(
        self,
        *,
        region_name: str | None = None,
        client: Any = None,
        with_decryption: bool = True,
    ) -> None:
        self._client = client if client is not None else boto3.client(
            "ssm", region_name=region_name
        )
        self.with_decryption = with_decryption

    def get(self, path: str) -> str:
        try:
            resp = self._client.get_parameter(
                Name=path, WithDecryption=self.with_decryption
            )
        except ClientError as e:
            self._raise_mapped(e, context=path)
        except BotoCoreError as e:
            msg = f"SSM connection error for {path}: {e}"
            raise TransientError(msg) from e
        return resp["Parameter"]["Value"]

    def get_batch(self, paths: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for chunk in _chunked(paths, _BATCH_LIMIT):
            try:
                resp = self._client.get_parameters(
                    Names=chunk, WithDecryption=self.with_decryption
                )
            except ClientError as e:
                self._raise_mapped(e, context=repr(chunk))
            except BotoCoreError as e:
                msg = f"SSM connection error for batch {chunk!r}: {e}"
                raise TransientError(msg) from e

            for param in resp.get("Parameters", []):
                out[param["Name"]] = param["Value"]
            invalid = resp.get("InvalidParameters", [])
            if invalid:
                msg = f"SSM parameters not found: {invalid}"
                raise SecretNotFoundError(msg)
        return out

    @staticmethod
    def _raise_mapped(e: ClientError, *, context: str) -> NoReturn:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ParameterNotFound":
            msg = f"SSM parameter not found: {context}"
            raise SecretNotFoundError(msg) from e
        if code in _AUTH_CODES:
            msg = f"SSM auth error ({code}) for {context}"
            raise AuthError(msg) from e
        if code in _TRANSIENT_CODES:
            msg = f"SSM transient error ({code}) for {context}"
            raise TransientError(msg) from e
        # Unknown ClientError — treat as transient by default; the retry
        # layer can decide. We don't want to silently swallow as success.
        msg = f"SSM error ({code or 'unknown'}) for {context}: {e}"
        raise TransientError(msg) from e


def _chunked(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
