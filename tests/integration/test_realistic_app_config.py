"""End-to-end: a realistic application config tree.

Models what a typical service would actually declare — stage, debug, nested
DB / cache / API sections — and exercises the full lifecycle: construction,
validation, lazy fetch, prefetch, refresh after rotation, concurrent reads.
"""

from __future__ import annotations

import json
import threading

import pytest

from vaultly import EnvBackend, MockBackend, Secret, SecretModel

# --------------------------------------------------------------------------- shape


class DbConfig(SecretModel):
    password: str = Secret("/{stage}/db/password", description="Postgres primary")
    pool_size: int = Secret("/{stage}/db/pool_size")


class CacheConfig(SecretModel):
    redis_url: str = Secret("/{stage}/cache/url")


class AppConfig(SecretModel):
    stage: str
    debug: bool = False
    db: DbConfig
    cache: CacheConfig
    openai_key: str = Secret("/services/openai/key")
    feature_flags: dict = Secret("/{stage}/flags")


# --------------------------------------------------------------------------- env-backend lifecycle


@pytest.fixture
def env_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lay out realistic env-var names mirroring a prod deployment."""
    monkeypatch.setenv("PROD_DB_PASSWORD", "super-secret")
    monkeypatch.setenv("PROD_DB_POOL_SIZE", "20")
    monkeypatch.setenv("PROD_CACHE_URL", "redis://prod-cache:6379/0")
    monkeypatch.setenv("SERVICES_OPENAI_KEY", "sk-prod-abc")
    monkeypatch.setenv("PROD_FLAGS", '{"new_billing": true, "dark_mode": false}')


def test_env_backend_full_app_construction(env_setup: None) -> None:
    cfg = AppConfig(
        stage="prod",
        debug=True,
        db={},
        cache={},
        backend=EnvBackend(),
    )

    # Scalars come straight from env.
    assert cfg.db.password == "super-secret"
    assert cfg.db.pool_size == 20
    assert isinstance(cfg.db.pool_size, int)

    assert cfg.cache.redis_url == "redis://prod-cache:6379/0"
    assert cfg.openai_key == "sk-prod-abc"

    assert cfg.feature_flags == {"new_billing": True, "dark_mode": False}
    assert isinstance(cfg.feature_flags, dict)


def test_env_backend_masking_at_dump(env_setup: None) -> None:
    cfg = AppConfig(
        stage="prod", db={}, cache={}, backend=EnvBackend()
    )
    # Force fetch of every secret.
    cfg.prefetch()

    dumped = cfg.model_dump()
    # All secret fields masked.
    assert dumped["db"] == {"password": "***", "pool_size": "***"}
    assert dumped["cache"] == {"redis_url": "***"}
    assert dumped["openai_key"] == "***"
    assert dumped["feature_flags"] == "***"
    # Non-secrets pass through.
    assert dumped["stage"] == "prod"
    assert dumped["debug"] is False

    # JSON path masks identically.
    j = cfg.model_dump_json()
    assert "super-secret" not in j
    assert "sk-prod-abc" not in j
    assert '"db":{"password":"***","pool_size":"***"}' in j


def test_env_backend_repr_never_leaks(env_setup: None) -> None:
    cfg = AppConfig(stage="prod", db={}, cache={}, backend=EnvBackend())
    cfg.prefetch()
    r = repr(cfg)
    for leak in ("super-secret", "sk-prod-abc", "redis://prod-cache"):
        assert leak not in r, f"{leak!r} leaked in repr"


def test_env_backend_validate_fetch_mode_prefetches_at_construction(
    env_setup: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Eager(SecretModel, validate="fetch"):
        stage: str
        db: DbConfig
        cache: CacheConfig
        openai_key: str = Secret("/services/openai/key")
        feature_flags: dict = Secret("/{stage}/flags")

    # Track what gets read.
    read_paths: list[str] = []

    class TrackingEnv(EnvBackend):
        def get(self, path: str, *, version: int | str | None = None) -> str:
            read_paths.append(path)
            return super().get(path)

    cfg = Eager(stage="prod", db={}, cache={}, backend=TrackingEnv())
    # Every secret was read at construction (no lazy delay).
    assert sorted(read_paths) == sorted(
        [
            "/prod/db/password",
            "/prod/db/pool_size",
            "/prod/cache/url",
            "/services/openai/key",
            "/prod/flags",
        ]
    )
    # And subsequent reads come from cache.
    read_paths.clear()
    _ = cfg.db.password
    _ = cfg.openai_key
    assert read_paths == []


# --------------------------------------------------------------------------- rotation lifecycle


def test_rotation_is_visible_after_refresh() -> None:
    """Simulate a real rotation: rewrite the backing store, refresh one field."""
    backend = MockBackend(
        {
            "/prod/db/password": "v1",
            "/prod/db/pool_size": "10",
            "/prod/cache/url": "redis://x:6379/0",
            "/services/openai/key": "sk-old",
            "/prod/flags": "{}",
        }
    )
    cfg = AppConfig(stage="prod", db={}, cache={}, backend=backend)

    assert cfg.db.password == "v1"
    assert cfg.openai_key == "sk-old"

    # Operator rotates the DB password.
    backend.data["/prod/db/password"] = "v2"

    # Cache still serves old value (TTL=None semantic).
    assert cfg.db.password == "v1"

    # Refresh from a parent — works on nested-owned secret too because both
    # share the root cache.
    assert cfg.db.refresh("password") == "v2"
    assert cfg.db.password == "v2"

    # api key untouched.
    assert cfg.openai_key == "sk-old"


def test_refresh_all_clears_entire_tree() -> None:
    backend = MockBackend(
        {
            "/prod/db/password": "v1",
            "/prod/db/pool_size": "10",
            "/prod/cache/url": "redis://x",
            "/services/openai/key": "sk",
            "/prod/flags": "{}",
        }
    )
    cfg = AppConfig(stage="prod", db={}, cache={}, backend=backend)
    cfg.prefetch()
    backend.reset_calls()

    cfg.refresh_all()
    # Read everything again — every secret refetches from backend.
    cfg.prefetch()
    assert sorted({p for p, _ in backend.calls}) == sorted(
        [
            "/prod/db/password",
            "/prod/db/pool_size",
            "/prod/cache/url",
            "/services/openai/key",
            "/prod/flags",
        ]
    )


# --------------------------------------------------------------------------- multi-stage


def test_two_stages_share_no_cache() -> None:
    """Two AppConfig instances with different stages keep separate cache trees."""
    backend_data: dict[str, str] = {
        "/dev/db/password": "dev-pw",
        "/dev/db/pool_size": "5",
        "/dev/cache/url": "redis://dev",
        "/dev/flags": "{}",
        "/prod/db/password": "prod-pw",
        "/prod/db/pool_size": "20",
        "/prod/cache/url": "redis://prod",
        "/prod/flags": "{}",
        "/services/openai/key": "sk-shared",
    }
    backend = MockBackend(backend_data)

    dev = AppConfig(stage="dev", db={}, cache={}, backend=backend)
    prod = AppConfig(stage="prod", db={}, cache={}, backend=backend)

    assert dev.db.password == "dev-pw"
    assert prod.db.password == "prod-pw"
    assert dev.openai_key == prod.openai_key == "sk-shared"

    # Both pulled the shared key — but each from its own cache (independent
    # SecretModel instances), so the same path was fetched twice.
    shared_calls = [c for c, _ in backend.calls if c == "/services/openai/key"]
    assert len(shared_calls) == 2


# --------------------------------------------------------------------------- contract for downstream code


def test_secret_value_is_native_type_not_wrapper() -> None:
    """A core ergonomic promise: `cfg.db.pool_size` is a plain `int`, not a
    SecretInt or proxy. Downstream libraries (psycopg, redis, httpx) must see
    real types."""
    backend = MockBackend(
        {
            "/prod/db/password": "pw",
            "/prod/db/pool_size": "20",
            "/prod/cache/url": "u",
            "/prod/flags": "{}",
            "/services/openai/key": "sk",
        }
    )
    cfg = AppConfig(stage="prod", db={}, cache={}, backend=backend)

    # `int` operations work directly.
    assert cfg.db.pool_size + 5 == 25
    assert isinstance(cfg.db.pool_size, int)
    # `dict` is a real dict.
    assert "new_billing" not in cfg.feature_flags  # data has empty dict
    # `str` is a real str (not SecretStr / wrapper).
    assert cfg.db.password.upper() == "PW"


# --------------------------------------------------------------------------- concurrent realistic load


def test_50_workers_realistic_read_pattern() -> None:
    """Simulate 50 worker threads each reading the full config many times.
    Should not error, should hit backend exactly once per unique path."""
    backend = MockBackend(
        {
            "/prod/db/password": "pw",
            "/prod/db/pool_size": "20",
            "/prod/cache/url": "redis://x",
            "/prod/flags": '{"a": 1}',
            "/services/openai/key": "sk",
        }
    )
    cfg = AppConfig(stage="prod", db={}, cache={}, backend=backend)

    errors: list[Exception] = []
    barrier = threading.Barrier(50)

    def worker() -> None:
        barrier.wait()
        try:
            for _ in range(100):
                assert cfg.db.password == "pw"
                assert cfg.db.pool_size == 20
                assert cfg.cache.redis_url == "redis://x"
                assert cfg.openai_key == "sk"
                assert cfg.feature_flags == {"a": 1}
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # Cold-cache thundering-herd protection: each unique path hits the
    # backend exactly once across 50x100=5000 reads.
    paths = {p for p, _ in backend.calls}
    assert paths == {
        "/prod/db/password",
        "/prod/db/pool_size",
        "/prod/cache/url",
        "/prod/flags",
        "/services/openai/key",
    }
    assert len(backend.calls) == 5  # one per path


# --------------------------------------------------------------------------- introspection


def test_model_dump_preserves_shape() -> None:
    backend = MockBackend(
        {
            "/prod/db/password": "pw",
            "/prod/db/pool_size": "20",
            "/prod/cache/url": "u",
            "/prod/flags": "{}",
            "/services/openai/key": "sk",
        }
    )
    cfg = AppConfig(stage="prod", debug=True, db={}, cache={}, backend=backend)
    dumped = cfg.model_dump()

    # Round-trip through JSON works (no non-serializable objects).
    json.dumps(dumped)

    assert dumped["stage"] == "prod"
    assert dumped["debug"] is True
    assert dumped["db"]["password"] == "***"
    assert dumped["cache"]["redis_url"] == "***"
