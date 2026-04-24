from __future__ import annotations

from pydantic.fields import FieldInfo

from vaultly.core.secret import MISSING, Secret, _SecretSpec


def test_secret_returns_fieldinfo():
    info = Secret("/db/password")
    assert isinstance(info, FieldInfo)


def test_secret_default_is_sentinel():
    info = Secret("/db/password")
    assert info.default is MISSING


def test_secret_spec_in_metadata():
    info = Secret("/db/password", ttl=60)
    specs = [m for m in info.metadata if isinstance(m, _SecretSpec)]
    assert len(specs) == 1
    assert specs[0].path == "/db/password"
    assert specs[0].ttl == 60
    assert specs[0].transform is None


def test_secret_carries_transform():
    t = str.upper
    info = Secret("/x", transform=t)
    [spec] = [m for m in info.metadata if isinstance(m, _SecretSpec)]
    assert spec.transform is t


def test_each_call_makes_a_fresh_spec():
    a = Secret("/a")
    b = Secret("/b")
    [spec_a] = [m for m in a.metadata if isinstance(m, _SecretSpec)]
    [spec_b] = [m for m in b.metadata if isinstance(m, _SecretSpec)]
    assert spec_a is not spec_b
