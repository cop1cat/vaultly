"""String -> T casts for secret values.

Backends return strings; we cast to the annotated field type. A custom
`transform` on the Secret overrides the default rules.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, get_origin

if TYPE_CHECKING:
    from collections.abc import Callable

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


def cast_value(raw: str, annotation: Any, transform: Callable[[str], Any] | None) -> Any:
    """Cast a raw string from a backend to the field's annotated type.

    `transform`, if provided, replaces the default rules entirely.
    """
    if transform is not None:
        return transform(raw)

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


def _cast_bool(raw: str) -> bool:
    low = raw.strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    msg = f"cannot parse bool from {raw!r}"
    raise ValueError(msg)
