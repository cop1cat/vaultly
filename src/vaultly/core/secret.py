"""The `Secret(...)` marker and its private spec record.

Usage:
    class AppConfig(SecretModel):
        db_password: str = Secret("/db/{stage}/password", ttl=60)

`Secret(...)` returns a Pydantic `FieldInfo` with a sentinel default, so the
field is optional at construction time. A `_SecretSpec` is stashed inside the
field's `metadata` list; `SecretModel.__pydantic_init_subclass__` reads it to
locate every secret field in the model.

See spike.py / PLAN.md for why this shape was chosen over `Annotated[T, ...]`
and over making `Secret` a `FieldInfo` subclass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

if TYPE_CHECKING:
    from collections.abc import Callable

# Sentinel stored as the default for every secret field. Never leaks to user
# code: `SecretModel.__getattribute__` intercepts reads before Pydantic would
# return it.
MISSING: Any = object()


class _SecretSpec:
    """Metadata for a single secret field. Lives in `FieldInfo.metadata`.

    `original_annotation` is captured the first time a model class wraps the
    field for masking. Subclasses that inherit the field see only the wrapped
    annotation in `field.annotation`; this slot lets them recover the
    original `int` / `dict` / `bool` / etc. for casting.
    """

    __slots__ = (
        "description",
        "original_annotation",
        "path",
        "transform",
        "ttl",
        "version",
    )

    def __init__(
        self,
        path: str,
        ttl: float | None,
        transform: Callable[[str], Any] | None,
        version: int | str | None,
        description: str | None,
    ) -> None:
        self.path = path
        self.ttl = ttl
        self.transform = transform
        self.version = version
        self.description = description
        self.original_annotation: Any = None

    def __repr__(self) -> str:
        bits = [f"path={self.path!r}"]
        if self.ttl is not None:
            bits.append(f"ttl={self.ttl!r}")
        if self.version is not None:
            bits.append(f"version={self.version!r}")
        if self.description is not None:
            bits.append(f"description={self.description!r}")
        return f"_SecretSpec({', '.join(bits)})"


def Secret(
    path: str,
    *,
    ttl: float | None = None,
    transform: Callable[[str], Any] | None = None,
    version: int | str | None = None,
    description: str | None = None,
) -> Any:
    """Declare a secret-backed field.

    Args:
        path: Backend path. `{var}` placeholders are filled from fields of
            the root `SecretModel` at fetch time.
        ttl: Cache lifetime in seconds. `None` = cache forever, `0` = never
            cache, `>0` = seconds.
        transform: Optional callable applied to the raw backend string,
            overriding the default type-based cast.
        version: Pin to a specific version of the secret. Backends that
            don't support versioning ignore this.
        description: Free-text description; surfaces in error messages and
            in the spec's `repr`. Useful for debugging large models.

    Returns:
        A Pydantic `FieldInfo` (typed as `Any` so it slots into `field: T = ...`
        declarations without complaints from type checkers).
    """
    spec = _SecretSpec(path, ttl, transform, version, description)
    info = Field(default=MISSING)
    info.metadata.append(spec)
    return info
