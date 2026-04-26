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
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PrivateAttr,
    SkipValidation,
    model_validator,
)

from vaultly.backends.base import (
    Backend,  # noqa: TC001  needed at runtime by pydantic for model_rebuild
)
from vaultly.core.cache import KeyedLocks, TTLCache
from vaultly.core.casts import cast_value
from vaultly.core.secret import MISSING, _SecretSpec
from vaultly.errors import (
    ConfigError,
    MissingContextVariableError,
    TransientError,
    VaultlyError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


ValidateMode = Literal["none", "paths", "fetch"]

_logger = logging.getLogger("vaultly")

_COPY_DENY_MSG = (
    "vaultly: copying a SecretModel is not supported — it would either "
    "share or duplicate the cache and break the _root linkage in nested "
    "trees. Construct a fresh instance with the same fields and backend "
    "instead. (`model_copy`, `copy.copy`, and `copy.deepcopy` all raise.)"
)


# True while the outermost vaultly post-validation pass is in progress.
# `model_validator(mode='after')` runs once per model in the tree (leaf to
# root); this flag lets only the outermost call wire/validate/prefetch and
# turns inner ones into no-ops.
_FINALIZE_IN_PROGRESS: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "vaultly_finalize_in_progress", default=False
)


def _extract_vars(path: str) -> list[str]:
    """Return `{var}` names used in a str.format-style template."""
    return [name for _, name, _, _ in string.Formatter().parse(path) if name]


def _cache_key(resolved_path: str, version: int | str | None) -> str:
    """Compose a cache key that distinguishes versions of the same path."""
    if version is None:
        return resolved_path
    return f"{resolved_path}@{version}"


def _describe(cls: type, name: str, spec: _SecretSpec) -> str:
    """Render a 'ClassName.field' label, optionally with the spec description."""
    label = f"{cls.__name__}.{name}"
    if spec.description:
        return f"{label} ({spec.description})"
    return label


def _cast_or_wrap(
    raw: str, ann: Any, spec: _SecretSpec, cls: type, name: str
) -> Any:
    """Call `cast_value` and wrap any non-Vaultly exception as `ConfigError`."""
    try:
        return cast_value(raw, ann, spec.transform)
    except VaultlyError:
        raise
    except Exception as e:
        msg = (
            f"failed to cast value for {_describe(cls, name, spec)}: "
            f"{type(e).__name__}: {e}"
        )
        raise ConfigError(msg) from e


class SecretModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    backend: Backend | None = Field(default=None, repr=False, exclude=True)

    _root: SecretModel | None = PrivateAttr(default=None)
    _cache: TTLCache = PrivateAttr(default_factory=TTLCache)
    _fetch_locks: KeyedLocks = PrivateAttr(default_factory=KeyedLocks)

    # Populated per-subclass by __pydantic_init_subclass__.
    __secret_fields__: ClassVar[dict[str, tuple[_SecretSpec, Any]]] = {}

    # Subclass-level config — set via class kwargs:
    #     class App(SecretModel, validate="fetch", stale_on_error=True): ...
    # `validate`: "none" skips checks; "paths" verifies every {var} resolves
    # against the root model (default); "fetch" additionally prefetches.
    # `stale_on_error`: on TransientError, fall back to expired cached value.
    _vaultly_validate: ClassVar[ValidateMode] = "paths"
    _vaultly_stale_on_error: ClassVar[bool] = False

    def __init_subclass__(
        cls,
        *,
        validate: ValidateMode | None = None,
        stale_on_error: bool | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        if validate is not None:
            cls._vaultly_validate = validate
        if stale_on_error is not None:
            cls._vaultly_stale_on_error = stale_on_error

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
            # When a parent class already wrapped this field, `field.annotation`
            # is the wrapped form (Annotated[SkipValidation[T], PlainSerializer]).
            # `_SecretSpec.original_annotation` is captured on first wrap and
            # is what `cast_value` must use.
            if spec.original_annotation is None:
                spec.original_annotation = field.annotation
                # Dynamic Annotated construction — hide behind Any so mypy
                # stays quiet. SkipValidation lets the sentinel through;
                # PlainSerializer masks the value in model_dump / JSON.
                any_annotated: Any = Annotated
                any_skip: Any = SkipValidation
                field.annotation = any_annotated[
                    any_skip[field.annotation],
                    PlainSerializer(
                        lambda _v: "***", return_type=str, when_used="always"
                    ),
                ]
                field.default = MISSING
                rebuild = True
            secrets[name] = (spec, spec.original_annotation)
        cls.__secret_fields__ = secrets
        if rebuild:
            cls.model_rebuild(force=True)

    # ------------------------------------------------------------------ post-validation

    @model_validator(mode="after")
    def _vaultly_finalize_internal(self) -> Self:
        """Wire the tree, validate paths, optionally prefetch.

        Named with a `_vaultly_…_internal` suffix to make accidental override
        in user subclasses unlikely. If you DO override this, the tree is
        no longer wired and nested fetch will fail. If you need a custom
        post-validator, declare it with a different name — both will run.

        `mode='after'` runs at the end of every successful validation —
        whether triggered by `Foo(...)` (which goes through `__init__` →
        `validate_python`) or by `Foo.model_validate({...})` (which calls
        `validate_python` directly). It also fires for every nested model;
        a ContextVar makes only the outermost call do work.
        """
        if _FINALIZE_IN_PROGRESS.get():
            return self
        token = _FINALIZE_IN_PROGRESS.set(True)
        try:
            self._wire_tree(self)
            mode: ValidateMode = getattr(type(self), "_vaultly_validate", "paths")
            if mode == "none":
                return self
            root_fields = self._context_field_names()
            try:
                self._validate_own_paths(root_fields)
            except MissingContextVariableError:
                # Our own secrets reference {vars} we don't have. Could be
                # a stand-alone bug, or this instance might still be wrapped
                # into a parent later (rare but possible after construction).
                # Either way, the first fetch will surface a clear error.
                return self
            self._validate_children_paths(root_fields)
            if mode == "fetch":
                self.prefetch()
        finally:
            _FINALIZE_IN_PROGRESS.reset(token)
        return self

    def _wire_tree(self, root: SecretModel) -> None:
        self._root = None if root is self else root
        for _, nested in self._iter_nested_secret_models():
            nested._wire_tree(root)

    def model_post_init(self, __context: Any) -> None:
        # No-op: wiring happens in the model_validator above so it runs on
        # both the `__init__` and `model_validate` construction paths.
        return

    def model_copy(self, *, update: Any = None, deep: bool = False) -> Self:
        raise NotImplementedError(_COPY_DENY_MSG)

    def __copy__(self) -> Self:
        # `copy.copy(model)` would share the same `_cache`, `_root`, and
        # `_fetch_locks` with the source — mutating one would mutate the
        # other. Block to enforce a clean construction story.
        raise NotImplementedError(_COPY_DENY_MSG)

    def __deepcopy__(self, memo: Any = None) -> Self:
        # Pydantic's default deepcopy currently fails on the threading.RLock
        # inside our cache, but that's accidental — pin the contract.
        raise NotImplementedError(_COPY_DENY_MSG)

    def __reduce__(self) -> Any:
        # Block pickle: a pickled SecretModel would carry the resolved
        # cleartext cache to disk or the wire. RLock currently happens to
        # make pickle fail at runtime, but pin the contract explicitly so
        # a future refactor doesn't silently enable a security footgun.
        msg = (
            "vaultly: pickling a SecretModel is not supported — it would "
            "serialize the in-memory cleartext cache. Ship the constructor "
            "inputs and reconstruct on the other side instead."
        )
        raise NotImplementedError(msg)

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
        resolved = self._resolve_path(cls, name, spec, root)
        cache_key = _cache_key(resolved, spec.version)
        # Fast path — no lock needed when a fresh value is already cached.
        try:
            return root._cache.get(cache_key)
        except KeyError:
            pass
        # Slow path — serialize concurrent backend fetches of the same key
        # so we don't stampede the backend on cold cache.
        with root._fetch_locks.for_key(cache_key):
            try:
                return root._cache.get(cache_key)
            except KeyError:
                pass
            return self._do_fetch(cls, name, spec, ann, root, resolved, cache_key)

    def _resolve_path(
        self, cls: type, name: str, spec: _SecretSpec, root: SecretModel
    ) -> str:
        del self  # only here so pyright stops complaining about staticmethod
        try:
            return spec.path.format(**root._context_values())
        except (KeyError, IndexError, AttributeError) as e:
            missing = e.args[0] if e.args else type(e).__name__
            msg = (
                f"cannot resolve path for {_describe(cls, name, spec)}: "
                f"{{{missing}}} is not a field on the root model "
                f"{type(root).__name__}"
            )
            raise MissingContextVariableError(msg) from None

    def _do_fetch(
        self,
        cls: type,
        name: str,
        spec: _SecretSpec,
        ann: Any,
        root: SecretModel,
        resolved: str,
        cache_key: str,
    ) -> Any:
        """Backend call + cast + cache write. Caller must hold the per-key lock."""
        cache = root._cache
        backend = root.backend
        if backend is None:
            msg = (
                f"cannot fetch {_describe(cls, name, spec)}: "
                f"no backend set on the root model"
            )
            raise ConfigError(msg)
        try:
            raw = backend.get(resolved, version=spec.version)
        except TransientError as transient_err:
            if getattr(type(root), "_vaultly_stale_on_error", False):
                try:
                    stale = cache.peek_expired(cache_key)
                except KeyError:
                    raise transient_err from None
                _logger.warning(
                    "vaultly: transient error fetching %s for %s; "
                    "returning stale cached value",
                    cache_key,
                    _describe(cls, name, spec),
                )
                return stale
            raise
        value = _cast_or_wrap(raw, ann, spec, cls, name)
        cache.set(cache_key, value, spec.ttl)
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
        """Invalidate `name` in the cache and re-fetch from the backend.

        Atomic against concurrent fetches: holds the per-key lock across
        invalidate + fetch so a parallel `_fetch` can't slip in and
        repopulate the cache with a stale value between the two steps.
        """
        cls = type(self)
        if name not in cls.__secret_fields__:
            msg = f"{name!r} is not a secret field on {cls.__name__}"
            raise ValueError(msg)
        spec, ann = cls.__secret_fields__[name]
        root = self._effective_root()
        resolved = self._resolve_path(cls, name, spec, root)
        cache_key = _cache_key(resolved, spec.version)
        with root._fetch_locks.for_key(cache_key):
            root._cache.invalidate(cache_key)
            return self._do_fetch(cls, name, spec, ann, root, resolved, cache_key)

    def refresh_all(self) -> None:
        """Invalidate every cached secret in this tree."""
        self._effective_root()._cache.clear()

    def prefetch(self) -> None:
        """Eagerly fetch every secret in the tree.

        Unversioned secrets are fetched in one `backend.get_batch` call;
        versioned secrets fall back to serial `get`. Safe to call multiple
        times; uses the root's cache as usual.
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

        batched: list[tuple[SecretModel, str, str]] = []
        versioned: list[tuple[SecretModel, str, str]] = []
        for entry in owners:
            owner, field_name, _ = entry
            spec, _ann = type(owner).__secret_fields__[field_name]
            (versioned if spec.version is not None else batched).append(entry)

        if batched:
            unique = list({p for _, _, p in batched})
            fetched = backend.get_batch(unique)
            for owner, field_name, resolved in batched:
                spec, ann = type(owner).__secret_fields__[field_name]
                raw = fetched[resolved]
                cls = type(owner)
                value = _cast_or_wrap(raw, ann, spec, cls, field_name)
                cache_key = _cache_key(resolved, None)
                # Hold the per-key lock so a concurrent _fetch can't slip
                # in between our backend call and our cache.set, hit the
                # backend a second time, and overwrite our value.
                with root._fetch_locks.for_key(cache_key):
                    root._cache.set(cache_key, value, spec.ttl)

        # Dedup versioned entries by (resolved_path, version). Two fields
        # pointing at the same versioned secret would otherwise hit the
        # backend twice with identical args.
        seen_versioned: set[tuple[str, int | str]] = set()
        for owner, field_name, resolved in versioned:
            spec, ann = type(owner).__secret_fields__[field_name]
            assert spec.version is not None  # filtered above
            key = (resolved, spec.version)
            if key in seen_versioned:
                continue
            seen_versioned.add(key)
            cls = type(owner)
            cache_key = _cache_key(resolved, spec.version)
            with root._fetch_locks.for_key(cache_key):
                raw = backend.get(resolved, version=spec.version)
                value = _cast_or_wrap(raw, ann, spec, cls, field_name)
                root._cache.set(cache_key, value, spec.ttl)

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
