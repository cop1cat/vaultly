"""SecretModel — the base class users inherit from.

Design notes live in PLAN.md and spike.py. Short version:

* `Secret(...)` returns a FieldInfo with a sentinel default; secrets are
  detected in `__pydantic_init_subclass__` and their annotations are wrapped
  with `SkipValidation` (so the sentinel passes validation) and
  `PlainSerializer` (so `model_dump` / JSON output masks them as `"***"`).
* `__getattribute__` is overridden *only* for declared secret fields; every
  other attribute access goes straight to Pydantic.
* Nested SecretModel fields share the root's backend and cache. A parent
  wires up each child's `_root` in its own `model_post_init`; the outermost
  `__init__` runs path validation and (optionally) prefetch once the whole
  tree is built.
"""

from __future__ import annotations

import contextvars
import logging
import string
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PrivateAttr, SkipValidation

from vaultly.backends.base import (
    Backend,  # noqa: TC001  needed at runtime by pydantic for model_rebuild
)
from vaultly.core.cache import TTLCache
from vaultly.core.casts import cast_value
from vaultly.core.secret import MISSING, _SecretSpec
from vaultly.errors import ConfigError, MissingContextVariableError, TransientError

if TYPE_CHECKING:
    from collections.abc import Iterator


ValidateMode = Literal["none", "paths", "fetch"]

_logger = logging.getLogger("vaultly")


# True while a SecretModel's __init__ is in progress on the current stack.
# Nested SecretModel hydration by Pydantic also goes through __init__; we use
# this flag so that only the outermost call runs path validation / prefetch.
_INIT_IN_PROGRESS: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "vaultly_init_in_progress", default=False
)


def _extract_vars(path: str) -> list[str]:
    """Return `{var}` names used in a str.format-style template."""
    return [name for _, name, _, _ in string.Formatter().parse(path) if name]


class SecretModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    backend: Backend | None = Field(default=None, repr=False, exclude=True)

    _root: SecretModel | None = PrivateAttr(default=None)
    _cache: TTLCache = PrivateAttr(default_factory=TTLCache)

    # Populated per-subclass by __pydantic_init_subclass__.
    __secret_fields__: ClassVar[dict[str, tuple[_SecretSpec, Any]]] = {}

    # Override on subclasses: "none" skips everything, "paths" checks that
    # every {var} resolves against the root's fields, "fetch" additionally
    # prefetches all secrets via backend.get_batch at construction time.
    _vaultly_validate: ClassVar[ValidateMode] = "paths"

    # When True, a TransientError from the backend falls back to the last
    # cached (possibly expired) value with a warning log. Opt-in — some
    # deployments prefer to fail fast on transient issues for security.
    _vaultly_stale_on_error: ClassVar[bool] = False

    # ------------------------------------------------------------------ subclass hook

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        secrets: dict[str, tuple[_SecretSpec, Any]] = {}
        rebuild = False
        for name, field in cls.model_fields.items():
            spec = next((m for m in field.metadata if isinstance(m, _SecretSpec)), None)
            if spec is None:
                continue
            secrets[name] = (spec, field.annotation)
            # Dynamic Annotated construction — hide behind Any so mypy/ruff
            # stay quiet; SkipValidation lets the sentinel through, and the
            # PlainSerializer masks the value in model_dump / JSON.
            any_annotated: Any = Annotated
            any_skip: Any = SkipValidation
            field.annotation = any_annotated[
                any_skip[field.annotation],
                PlainSerializer(lambda _v: "***", return_type=str, when_used="always"),
            ]
            field.default = MISSING
            rebuild = True
        cls.__secret_fields__ = secrets
        if rebuild:
            cls.model_rebuild(force=True)

    # ------------------------------------------------------------------ construction

    def __init__(self, **data: Any) -> None:
        if _INIT_IN_PROGRESS.get():
            # Being hydrated by a parent's __init__ — parent owns validation.
            super().__init__(**data)
            return
        token = _INIT_IN_PROGRESS.set(True)
        try:
            super().__init__(**data)
        finally:
            _INIT_IN_PROGRESS.reset(token)
        mode: ValidateMode = getattr(type(self), "_vaultly_validate", "paths")
        if mode == "none":
            return
        root_fields = self._context_field_names()
        try:
            self._validate_own_paths(root_fields)
        except MissingContextVariableError:
            # *Our own* secrets reference {vars} we don't have. Defer: the
            # instance may become a nested child of a parent that provides
            # them. If never wired, the first `fetch` surfaces the error.
            return
        # Errors in nested children are bugs in *this* tree — never defer.
        self._validate_children_paths(root_fields)
        if mode == "fetch":
            self.prefetch()

    def model_post_init(self, __context: Any) -> None:
        # Wire every immediate nested SecretModel field to this instance's
        # effective root (either our own _root if we were wired by a parent,
        # or ourselves if we are the root).
        root = self._root if self._root is not None else self
        for name, val in self._iter_nested_secret_models():
            val._root = root
            _ = name  # for potential future diagnostics

    def _iter_nested_secret_models(self) -> Iterator[tuple[str, SecretModel]]:
        cls = type(self)
        secret_names = cls.__secret_fields__
        for name in cls.model_fields:
            if name in secret_names or name == "backend":
                continue
            val = self.__dict__.get(name)
            if isinstance(val, SecretModel):
                yield name, val

    # ------------------------------------------------------------------ path validation

    def _context_field_names(self) -> set[str]:
        cls = type(self)
        return {
            name
            for name in cls.model_fields
            if name != "backend"
            and name not in cls.__secret_fields__
            and not isinstance(self.__dict__.get(name), SecretModel)
        }

    def _validate_own_paths(self, root_fields: set[str]) -> None:
        cls = type(self)
        for name, (spec, _) in cls.__secret_fields__.items():
            for var in _extract_vars(spec.path):
                if var not in root_fields:
                    msg = (
                        f"secret field {cls.__name__}.{name} references "
                        f"{{{var}}}, but no such field exists on the root model"
                    )
                    raise MissingContextVariableError(msg)

    def _validate_children_paths(self, root_fields: set[str]) -> None:
        for _, nested in self._iter_nested_secret_models():
            nested._validate_own_paths(root_fields)
            nested._validate_children_paths(root_fields)

    # ------------------------------------------------------------------ attribute access

    def __getattribute__(self, name: str) -> Any:
        if not name.startswith("_") and name != "backend":
            cls = object.__getattribute__(self, "__class__")
            secrets = getattr(cls, "__secret_fields__", {})
            if name in secrets:
                return object.__getattribute__(self, "_fetch")(name)
        return super().__getattribute__(name)

    def _fetch(self, name: str) -> Any:
        cls = type(self)
        spec, ann = cls.__secret_fields__[name]
        root = self._effective_root()
        try:
            resolved = spec.path.format(**root._context_values())
        except KeyError as e:
            missing = e.args[0] if e.args else "?"
            msg = (
                f"cannot resolve path for {cls.__name__}.{name}: "
                f"{{{missing}}} is not a field on the root model "
                f"{type(root).__name__}"
            )
            raise MissingContextVariableError(msg) from None
        cache = root._cache
        try:
            return cache.get(resolved)
        except KeyError:
            pass
        backend = root.backend
        if backend is None:
            msg = (
                f"cannot fetch {cls.__name__}.{name}: no backend set on the root model"
            )
            raise ConfigError(msg)
        try:
            raw = backend.get(resolved)
        except TransientError as transient_err:
            if getattr(type(root), "_vaultly_stale_on_error", False):
                try:
                    stale = cache.peek_expired(resolved)
                except KeyError:
                    raise transient_err from None
                _logger.warning(
                    "vaultly: transient error fetching %s for %s.%s; "
                    "returning stale cached value",
                    resolved,
                    cls.__name__,
                    name,
                )
                return stale
            raise
        value = cast_value(raw, ann, spec.transform)
        cache.set(resolved, value, spec.ttl)
        return value

    def _effective_root(self) -> SecretModel:
        return self._root if self._root is not None else self

    def _context_values(self) -> dict[str, Any]:
        """Scalar (non-SecretModel, non-secret) fields usable in path templates."""
        cls = type(self)
        out: dict[str, Any] = {}
        for name in cls.model_fields:
            if name == "backend" or name in cls.__secret_fields__:
                continue
            val = self.__dict__.get(name)
            if isinstance(val, SecretModel):
                continue
            out[name] = val
        return out

    # ------------------------------------------------------------------ masking

    def __repr_args__(self) -> Iterator[tuple[str | None, Any]]:
        secrets = type(self).__secret_fields__
        for k, v in super().__repr_args__():
            if k in secrets:
                yield k, "***"
            else:
                yield k, v

    # ------------------------------------------------------------------ public API

    def refresh(self, name: str) -> Any:
        """Invalidate `name` in the cache and re-fetch from the backend."""
        cls = type(self)
        if name not in cls.__secret_fields__:
            msg = f"{name!r} is not a secret field on {cls.__name__}"
            raise ValueError(msg)
        spec, _ = cls.__secret_fields__[name]
        root = self._effective_root()
        resolved = spec.path.format(**root._context_values())
        root._cache.invalidate(resolved)
        return self._fetch(name)

    def refresh_all(self) -> None:
        """Invalidate every cached secret in this tree."""
        self._effective_root()._cache.clear()

    def prefetch(self) -> None:
        """Eagerly fetch every secret in the tree via `backend.get_batch`.

        Safe to call multiple times; uses the root's cache as usual.
        """
        root = self._effective_root()
        backend = root.backend
        if backend is None:
            msg = "cannot prefetch: no backend set on the root model"
            raise ConfigError(msg)

        owners: list[tuple[SecretModel, str, str]] = []
        self._collect_paths(root._context_values(), owners)
        if not owners:
            return
        unique = list({p for _, _, p in owners})
        fetched = backend.get_batch(unique)
        for owner, field_name, resolved in owners:
            spec, ann = type(owner).__secret_fields__[field_name]
            raw = fetched[resolved]
            value = cast_value(raw, ann, spec.transform)
            root._cache.set(resolved, value, spec.ttl)

    def _collect_paths(
        self,
        ctx: dict[str, Any],
        out: list[tuple[SecretModel, str, str]],
    ) -> None:
        cls = type(self)
        for name, (spec, _) in cls.__secret_fields__.items():
            out.append((self, name, spec.path.format(**ctx)))
        for _, nested in self._iter_nested_secret_models():
            nested._collect_paths(ctx, out)
