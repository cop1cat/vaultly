"""Edge cases for the {var} path templating: positional fields, attribute
access, escaped braces, etc. We want all of them to either work cleanly or
produce a `MissingContextVariableError` with a useful message — never an
opaque `KeyError`/`IndexError`."""

from __future__ import annotations

import pytest

from vaultly import MissingContextVariableError, Secret, SecretModel
from vaultly.testing.mock import MockBackend


def test_escaped_braces_pass_through():
    """`{{` and `}}` are literal braces in str.format and should survive."""

    class App(SecretModel):
        path: str = "x"
        secret: str = Secret("/literal/{{not_a_var}}/{path}")

    b = MockBackend({"/literal/{not_a_var}/x": "v"})
    c = App(path="x", backend=b)
    assert c.secret == "v"


def test_no_vars_at_all():
    class App(SecretModel):
        s: str = Secret("/static/path")

    c = App(backend=MockBackend({"/static/path": "v"}))
    assert c.s == "v"


def test_multiple_vars_same_name():
    class App(SecretModel):
        x: str = "X"
        s: str = Secret("/{x}/{x}/end")

    c = App(backend=MockBackend({"/X/X/end": "v"}))
    assert c.s == "v"


def test_positional_var_surfaces_clean_error():
    """`{0}` references a positional arg — we only support named ones."""

    class App(SecretModel):
        s: str = Secret("/{0}/x")

    c = App(backend=MockBackend({}))
    with pytest.raises(MissingContextVariableError):
        _ = c.s


def test_attribute_access_in_template():
    """`{x.y}` is a string.Formatter feature; not supported, must report
    missing-variable cleanly rather than KeyError/AttributeError."""

    class App(SecretModel):
        s: str = Secret("/{x.y}/end")

    c = App(backend=MockBackend({}))
    with pytest.raises(MissingContextVariableError):
        _ = c.s


def test_index_access_in_template():
    class App(SecretModel):
        s: str = Secret("/{x[0]}/end")

    c = App(backend=MockBackend({}))
    with pytest.raises(MissingContextVariableError):
        _ = c.s
