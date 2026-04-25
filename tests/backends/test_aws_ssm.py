from __future__ import annotations

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws
import pytest

from vaultly.backends.aws_ssm import AWSSSMBackend
from vaultly.errors import AuthError, SecretNotFoundError, TransientError


@pytest.fixture(autouse=True)
def _aws_creds(monkeypatch):
    """moto needs *some* AWS creds in env to keep boto3 from trying real auth."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


def _put(client, name: str, value: str, *, secure: bool = True) -> None:
    client.put_parameter(
        Name=name,
        Value=value,
        Type="SecureString" if secure else "String",
        Overwrite=True,
    )


@mock_aws
def test_get_returns_decrypted_value():
    client = boto3.client("ssm", region_name="us-east-1")
    _put(client, "/db/prod/password", "s3cr3t")
    b = AWSSSMBackend(region_name="us-east-1")
    assert b.get("/db/prod/password") == "s3cr3t"


@mock_aws
def test_get_missing_raises_secret_not_found():
    b = AWSSSMBackend(region_name="us-east-1")
    with pytest.raises(SecretNotFoundError, match="not found"):
        b.get("/nope")


@mock_aws
def test_get_batch():
    client = boto3.client("ssm", region_name="us-east-1")
    _put(client, "/a", "1")
    _put(client, "/b", "2")
    _put(client, "/c", "3")
    b = AWSSSMBackend(region_name="us-east-1")
    assert b.get_batch(["/a", "/b", "/c"]) == {"/a": "1", "/b": "2", "/c": "3"}


@mock_aws
def test_get_batch_chunks_at_ssm_limit():
    client = boto3.client("ssm", region_name="us-east-1")
    paths = [f"/p/{i}" for i in range(15)]
    for i, p in enumerate(paths):
        _put(client, p, str(i))

    calls: list[list[str]] = []
    real = client.get_parameters

    def spy(**kw):
        calls.append(list(kw["Names"]))
        return real(**kw)

    # boto3 client is a generated proxy; assign through setattr to avoid
    # type checkers complaining about an unknown attribute slot.
    object.__setattr__(client, "get_parameters", spy)
    b = AWSSSMBackend(client=client)
    out = b.get_batch(paths)

    assert out == {p: str(i) for i, p in enumerate(paths)}
    # 15 paths → 10 + 5
    assert [len(c) for c in calls] == [10, 5]


@mock_aws
def test_get_batch_invalid_parameters_raises():
    client = boto3.client("ssm", region_name="us-east-1")
    _put(client, "/a", "1")
    b = AWSSSMBackend(region_name="us-east-1")
    with pytest.raises(SecretNotFoundError, match="not found"):
        b.get_batch(["/a", "/missing"])


def test_access_denied_maps_to_auth_error():
    """Synthetic ClientError — moto doesn't easily simulate denied access."""

    class FakeClient:
        def get_parameter(self, **kw):
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                "GetParameter",
            )

    b = AWSSSMBackend(client=FakeClient())
    with pytest.raises(AuthError, match="AccessDeniedException"):
        b.get("/x")


def test_throttling_maps_to_transient_error():
    class FakeClient:
        def get_parameter(self, **kw):
            raise ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
                "GetParameter",
            )

    b = AWSSSMBackend(client=FakeClient())
    with pytest.raises(TransientError, match="ThrottlingException"):
        b.get("/x")


def test_unknown_clienterror_treated_as_transient():
    class FakeClient:
        def get_parameter(self, **kw):
            raise ClientError(
                {"Error": {"Code": "WeirdErrorWeNeverHeardOf", "Message": "?"}},
                "GetParameter",
            )

    b = AWSSSMBackend(client=FakeClient())
    with pytest.raises(TransientError, match="WeirdErrorWeNeverHeardOf"):
        b.get("/x")
