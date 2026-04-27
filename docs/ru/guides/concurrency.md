# Конкурентность

vaultly рассчитан на типовой Python-сервис: много потоков делят один
инстанс конфига, возможно внутри async event-loop, возможно под
нагрузкой.

## Threading

`SecretModel` безопасно делить между потоками. Чтения и записи во
внутренний кэш защищены `threading.Lock`. Cold-cache фетчи защищены
per-key `threading.RLock` — 100 потоков, одновременно запросивших
один и тот же холодный секрет, дадут **ровно один** вызов бэкенда.

```python
config = AppConfig(stage="prod", backend=...)

# Безопасно из любого числа потоков.
db_pw = config.db_password
api_k = config.api_key
```

Что защищено:

- `cache.get` / `cache.set` / `cache.invalidate` / `cache.peek_expired`
- Cold-cache fetch (per-key lock на резолвленный ключ)
- `refresh(name)` (per-key lock на `invalidate + fetch`)
- `prefetch()` (захватывает все per-key locks до batch-вызова)

Что **не** защищено:

- Мутация ваших не-секретных полей (например `config.stage = "dev"`) —
  Pydantic их не лочит. Если нужна настоящая immutability, ставьте
  `model_config = ConfigDict(frozen=True)` или меняйте только при
  старте.
- Замена поля `backend` (`config.backend = new_backend`) семантически
  не поддерживается; пересоздайте модель.

## Производительность hot-path

Cache hit берёт только лёгкий лок самого кэша, не per-key — поэтому
hot-чтения масштабируются по потокам. Integration-тест
`test_warm_cache_hot_reads_dont_serialize` прогоняет 200 000 чтений на
20 потоках меньше чем за секунду.

Регрессия, которая бы заставила hot-чтения сериализоваться (например,
если всегда брать per-key lock), увеличила бы это число в 100+ раз.

## Asyncio

vaultly **синхронный**. Нет `aget`, нет `AsyncBackend`. Вызов
`config.db_password` блокирует event-loop на время раунд-трипа в
бэкенд.

Для типичных конфигов, которые загружаются на старте
(`validate="fetch"`) и потом отдаются из кэша — это нормально.
Единственное blocking-обращение происходит при бутстрапе.

Если нужно фетчить лениво во время обработки запроса и блокировать
loop нельзя — оборачивайте в `asyncio.to_thread`:

```python
import asyncio

# Внутри async-хендлера:
db_pw = await asyncio.to_thread(lambda: config.db_password)
```

Нативный async API запланирован на v0.2.

## Границы процессов

vaultly не разделяет состояние между процессами. У каждого процесса
свой кэш:

- `multiprocessing` — каждый воркер конструирует свою модель со своим
  кэшем.
- `gunicorn` (forking) — child-процессы наследуют модель от родителя;
  кэш делится через fork-copy, но как snapshot. Мутации одним child'ом
  не пробрасываются другим. Если нужна per-child ротация — конструируйте
  модель в каждом child'е.
- `gevent` / `eventlet` — `threading.Lock` под monkey-patching'ом
  становится green-lock'ом. Должно работать, но специально не
  тестировалось.

## Fork safety

vaultly не устанавливает `os.register_at_fork`-обработчики. Если
вы fork'аетесь после касания кэша, child наследует snapshot
in-memory состояния, включая лок-объекты. Best practice: создавайте
`SecretModel` *после* fork'а (в каждом воркере), а не до.

## Multi-tenant паттерны

Если приложение мультитенантное и подставляет tenant id в путь:

```python
class TenantConfig(SecretModel):
    tenant_id: str
    api_key: str = Secret("/tenants/{tenant_id}/api_key", ttl=300)
```

Вы скорее всего хотите по одному инстансу `TenantConfig` на каждый
тенант, со своим кэшем. Дефолтные cache + lock dict'ы растут по мере
появления новых резолвленных путей — bounded `tenant_count ×
secrets_per_tenant`.

При очень большом числе тенантов периодически освобождайте память:

```python
# Удалить конкретную запись.
config._cache.invalidate(resolved_path)
config._fetch_locks.discard(resolved_path)

# Или очистить всё (например в конце запроса).
config._cache.clear()
config._fetch_locks.clear()
```

Это приватные API, но стабильные для этого use case.
