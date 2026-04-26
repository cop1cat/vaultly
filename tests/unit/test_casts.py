from __future__ import annotations

import pytest

from vaultly.core.casts import cast_value


def test_str_passthrough():
    assert cast_value("hello", str, None) == "hello"


def test_int():
    assert cast_value("42", int, None) == 42


def test_float():
    assert cast_value("3.14", float, None) == 3.14


@pytest.mark.parametrize("raw", ["true", "TRUE", "True", "1", "yes", "on", " on "])
def test_bool_truthy(raw):
    assert cast_value(raw, bool, None) is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "off", " OFF "])
def test_bool_falsy(raw):
    assert cast_value(raw, bool, None) is False


def test_bool_invalid():
    with pytest.raises(ValueError, match="cannot parse bool"):
        cast_value("maybe", bool, None)


def test_dict_json():
    assert cast_value('{"a": 1}', dict, None) == {"a": 1}


def test_list_json():
    assert cast_value("[1, 2, 3]", list, None) == [1, 2, 3]


def test_transform_overrides_default():
    assert cast_value("42", int, lambda s: s.upper()) == "42"


def test_transform_on_unknown_type_wins():
    class Weird:
        pass

    assert cast_value("x", Weird, lambda s: s + "!") == "x!"


def test_unknown_type_falls_back_to_raw_string():
    class Weird:
        pass

    assert cast_value("raw", Weird, None) == "raw"


def test_generic_dict_annotation():
    assert cast_value('{"k": "v"}', dict[str, str], None) == {"k": "v"}
