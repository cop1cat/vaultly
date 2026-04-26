"""Shared fixtures for integration tests.

Integration tests exercise full lifecycles end-to-end (multi-component
stacks, realistic timing, concurrent access). Each module focuses on one
backend or one cross-cutting concern.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _aws_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure boto3 doesn't reach for ~/.aws/credentials in moto-backed tests."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
