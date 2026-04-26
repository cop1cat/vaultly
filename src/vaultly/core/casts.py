"""String -> T casts for secret values.

Backends return strings; we cast to the annotated field type. A custom
`transform` on the Secret overrides the default rules.
"""

from __future__ import annotations

import json
import types
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

if TYPE_CHECKING:
    from collections.abc import Callable

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})

_NoneType = type(None)


def cast_value(raw: str, annotation: Any, transform: Callable[[str], Any] | None) -> Any:
    """Cast a raw string from a backend to the field's annotated type.

    `transform`, if provided, replaces the default rules entirely.

    `Optional[T]` / `T | None` are unwrapped to their non-None branch before
    casting — a backend that returns a string never produces `None`, so the
    `None` half of the union is irrelevant here.
    """
    if transform is not None:
        return transform(raw)

    annotation = _unwrap_optional(annotation)
    target = get_origin(annotation) or annotation

    if target is str:
        return raw
    if target is int:
        return int(raw)
    if target is float:
        return float(raw)
    if target is bool:
        return _cast_bool(raw)
    if target in (dict, list):
        return json.loads(raw)
    return raw


def _unwrap_optional(annotation: Any) -> Any:
    """Strip a single `None` arm from `T | None` / `Optional[T]`.

    Returns the underlying `T` for two-arm unions where one arm is `None`;
    leaves anything else (including three-arm unions like `int | str | None`)
    untouched, since we can't pick a single cast for those.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        non_none = [a for a in get_args(annotation) if a is not _NoneType]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _cast_bool(raw: str) -> bool:
    low = raw.strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    msg = f"cannot parse bool from {raw!r}"
    raise ValueError(msg)
