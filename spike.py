# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.6"]
# ///
"""
Spike: проверить что Pydantic v2 + `field: T = Secret(...)` + lazy fetch +
маскирование уживаются в одной модели.

API (подтверждён спайком):

    class AppConfig(SecretModel):
        stage: str = "dev"
        db_password: str = Secret("/db/{stage}/password", ttl=60)

Не `Annotated[str, Secret(...)]`, а именно присваивание — тогда pyright
видит тип поля как `str` (проекция `T`), а наличие `= <expr>` делает поле
optional при конструировании без плагинов. Работает благодаря встроенному
`@dataclass_transform` на `BaseModel`; **свой** transform не нужен —
он ломает `pydantic.mypy`-плагин.

Итог: pyright 0 errors, mypy + `pydantic.mypy` 0 errors, рантайм зелёный.

Цели:
1. В одной модели мешать обычные pydantic-поля и `= Secret(...)`-поля.
2. Тип поля для mypy/IDE — T. Доступ возвращает T.
3. repr(config) и model_dump() маскируют секреты.
4. Интерполяция {var} из полей самой модели.
5. TTL cache — второй доступ не зовёт backend.
6. Публичный kwarg backend=, видимый для type checker.
7. Секретные поля не required при конструировании (type checker).
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, get_origin

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PrivateAttr, SkipValidation

_MISSING: Any = object()


class _SecretSpec:
    """Plain-marker в metadata — то, по чему мы детектим secret-поле."""

    def __init__(self, path: str, ttl: float | None, transform: Any) -> None:
        self.path = path
        self.ttl = ttl
        self.transform = transform

    def __repr__(self) -> str:
        return f"_SecretSpec({self.path!r})"


def Secret(
    path: str,
    *,
    ttl: float | None = None,
    transform=None,
) -> Any:
    """Маркер secret-поля. Usage: `field: T = Secret("/path", ttl=60)`.

    Возвращает FieldInfo-дефолт с sentinel-значением; Pydantic трактует
    поле как опциональное (в рантайме реальное значение подставит lazy
    fetch). Pyright видит присваивание `= Secret(...)` и с
    `@dataclass_transform(field_specifiers=(..., Secret))` на `SecretModel`
    тоже считает поле опциональным при конструировании — без плагинов.

    В metadata кладётся `_SecretSpec` — по нему поле детектится в
    `__pydantic_init_subclass__`.
    """
    spec = _SecretSpec(path, ttl, transform)
    info = Field(default=_MISSING)
    info.metadata.append(spec)
    return info


class Backend:
    def get(self, path: str) -> str:
        raise NotImplementedError


class DictBackend(Backend):
    def __init__(self, data: dict[str, str]) -> None:
        self.data = data
        self.calls: list[str] = []

    def get(self, path: str) -> str:
        self.calls.append(path)
        return self.data[path]


class SecretModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    backend: Backend = Field(repr=False, exclude=True)
    _cache: dict[str, tuple[Any, float | None]] = PrivateAttr(default_factory=dict)

    __secret_fields__: dict[str, tuple[_SecretSpec, Any]] = {}

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
            # SkipValidation — чтобы сентинел прошёл валидацию.
            # PlainSerializer — маскирует значение в model_dump / json.
            # Динамическая конструкция Annotated: mypy не умеет это
            # проверять, прячем за Any-алиасом.
            _A: Any = Annotated
            _SV: Any = SkipValidation
            field.annotation = _A[
                _SV[field.annotation],
                PlainSerializer(lambda _v: "***", return_type=str, when_used="always"),
            ]
            field.default = _MISSING
            rebuild = True
        cls.__secret_fields__ = secrets
        if rebuild:
            cls.model_rebuild(force=True)

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
        resolved = spec.path.format(**self._context_values())
        now = time.monotonic()
        cache = self._cache
        if resolved in cache:
            value, expires = cache[resolved]
            if expires is None or now < expires:
                return value
        raw = self.backend.get(resolved)
        value = self._cast(raw, ann, spec)
        if spec.ttl is None:
            expires = None
        elif spec.ttl == 0:
            return value
        else:
            expires = now + spec.ttl
        cache[resolved] = (value, expires)
        return value

    @staticmethod
    def _cast(raw: str, ann: Any, spec: _SecretSpec) -> Any:
        if spec.transform is not None:
            return spec.transform(raw)
        origin = get_origin(ann)
        target = origin if origin is not None else ann
        if target is str:
            return raw
        if target is int:
            return int(raw)
        if target is float:
            return float(raw)
        if target is bool:
            low = raw.strip().lower()
            if low in ("true", "1", "yes", "on"):
                return True
            if low in ("false", "0", "no", "off"):
                return False
            raise ValueError(f"cannot parse bool from {raw!r}")
        if target in (dict, list):
            return json.loads(raw)
        return raw

    def _context_values(self) -> dict[str, Any]:
        cls = type(self)
        secrets = cls.__secret_fields__
        out: dict[str, Any] = {}
        data = self.__dict__
        for name in cls.model_fields:
            if name in secrets or name == "backend":
                continue
            out[name] = data[name]
        return out

    def __repr_args__(self):
        secrets = type(self).__secret_fields__
        for k, v in super().__repr_args__():
            if k in secrets:
                yield k, "***"
            else:
                yield k, v


# ---------- тест ----------


class AppConfig(SecretModel):
    stage: str = "dev"
    debug: bool = False
    db_password: str = Secret("/db/{stage}/password", ttl=60)
    api_key: str = Secret("/services/openai/key")
    max_conns: int = Secret("/db/{stage}/max_conns")
    feature_flags: dict = Secret("/flags/{stage}")


def main() -> None:
    backend = DictBackend(
        {
            "/db/prod/password": "s3cr3t",
            "/services/openai/key": "sk-abc",
            "/db/prod/max_conns": "42",
            "/flags/prod": '{"beta": true, "dark_mode": false}',
        }
    )
    config = AppConfig(stage="prod", debug=True, backend=backend)

    print("1. repr(config):", repr(config))
    print("2. model_dump:   ", config.model_dump())

    pw = config.db_password
    print("3. db_password:  ", pw, "(type:", type(pw).__name__ + ")")

    key = config.api_key
    print("4. api_key:      ", key)

    n = config.max_conns
    print("5. max_conns:    ", n, "(type:", type(n).__name__ + ")")

    flags = config.feature_flags
    print("6. feature_flags:", flags, "(type:", type(flags).__name__ + ")")

    _ = config.db_password
    _ = config.db_password
    print("7. backend calls:", backend.calls)

    # --- runtime invariants ---
    assert repr(config).count("s3cr3t") == 0
    assert "s3cr3t" not in str(config.model_dump())
    assert pw == "s3cr3t"
    assert isinstance(pw, str)
    assert key == "sk-abc"
    assert n == 42
    assert isinstance(n, int)
    assert flags == {"beta": True, "dark_mode": False}
    assert isinstance(flags, dict)
    assert backend.calls == [
        "/db/prod/password",
        "/services/openai/key",
        "/db/prod/max_conns",
        "/flags/prod",
    ], backend.calls

    # --- статические типы (для mypy/pyright) ---
    # Поле видится как T, не как Secret[T]:
    pw_typed: str = config.db_password
    n_typed: int = config.max_conns
    flags_typed: dict = config.feature_flags
    _ = (pw_typed, n_typed, flags_typed)

    print("\nOK")


if __name__ == "__main__":
    main()
