# Конкурентность

vaultly построен под типичную форму Python-сервиса: много потоков делят
один инстанс конфига, возможно внутри async event-loop, возможно под
нагрузкой.

## Threading

`SecretModel` безопасно делить между потоками. Чтения и записи во
внутренний кэш защищены `threading.Lock`; cold-cache фетчи защищены
per-key `threading.RLock`, поэтому 100 потоков, запрашивающих один
секрет одновременно, дают **ровно один** вызов бэкенда.

```python
config = AppConfig(stage="prod", backend=...)

# Безопасно из любого числа потоков.
db_pw = config.db_password
api_k = config.api_key
```

Что защищено:

- `cache.get` / `cache.set` / `cache.invalidate` / `cache.peek_expired`
- Cold-cache fetch последовательность (lock на резолвленный ключ кэша)
- `refresh(name)` (держит per-key lock на `invalidate + fetch`)
- `prefetch()` (захватывает каждый per-key lock до batch-вызова)

Что **не** защищено:

- Мутация ваших собственных не-секретных полей (например `config.stage =
  "dev"`) — Pydantic не лочит их; нужен `model_config =
  ConfigDict(frozen=True)` для настоящей immutability или делать это
  только при старте.
- Замена поля `backend` (`config.backend = new_backend`) — семантически
  не поддерживается; перезагрузите конфиг пересозданием модели.

## Hot-path производительность

Cache **hits** берут только лёгкий cache lock, не per-key. Они
масштабируются по потокам. Integration-тест
`test_warm_cache_hot_reads_dont_serialize` подтверждает, что 200 000
чтений на 20 потоках завершаются well under секунды.

Регрессия, при которой hot-чтения начнут сериализоваться (например, если
всегда брать per-key lock), раздула бы это число в 100+ раз.

## Asyncio

vaultly **синхронный**. Нет `aget`, нет `AsyncBackend`. Вызовы
`config.db_password` блокируют event-loop на время backend round-trip.

Для типичных конфигов, которые загружаются при старте (`validate="fetch"`)
и далее обслуживаются из кэша, это нормально — единственный блокирующий
вызов — на бутстрапе.

Для конфигов с ленивой загрузкой во время обработки запроса, где
блокировать нельзя — оборачивайте в `asyncio.to_thread`:

```python
import asyncio

# Внутри async-хендлера:
db_pw = await asyncio.to_thread(lambda: config.db_password)
```

Нативный async API в roadmap'е v0.2.

## Границы процессов

vaultly не разделяет состояние между процессами. Каждый процесс — свой
кэш:

- `multiprocessing` — воркеры конструируют каждый свою модель; каждый
  имеет свой бэкенд + кэш.
- `gunicorn` (forking) — child-процессы наследуют модель от родителя;
  кэш *делится* через fork-copy, но только как snapshot. Мутации одним
  child'ом не пробрасываются. Конструируйте в каждом child'е, если
  полагаетесь на per-child ротацию.
- `gevent` / `eventlet` — `threading.Lock` от vaultly становится
  green-lock'ом под monkey-patching'ом. Должно работать, но специально
  не тестировалось.

## Fork safety

vaultly сейчас не устанавливает `os.register_at_fork`-обработчики. Если
вы fork'аетесь после касания кэша, child наследует snapshot in-memory
состояния, включая lock'и. Best practice: конструируйте `SecretModel`
*после* fork (в каждом воркере), не до.

## Per-tenant паттерны

Если ваше приложение мультитенантное, интерполируя tenant id в путь:

```python
class TenantConfig(SecretModel):
    tenant_id: str
    api_key: str = Secret("/tenants/{tenant_id}/api_key", ttl=300)
```

Вы скорее всего хотите один инстанс `TenantConfig` на тенант, каждый со
своим кэшом. Дефолтные cache + lock dict-ы растут по мере появления
новых резолвленных путей — bounded `tenant_count × secrets_per_tenant`.

Для очень высокого числа тенантов периодически освобождайте память:

```python
# Удалить конкретную запись.
config._cache.invalidate(resolved_path)
config._fetch_locks.discard(resolved_path)

# Или обнулить всё (например в конце запроса).
config._cache.clear()
config._fetch_locks.clear()
```

Это приватные API, но стабильные для этого use case'а.
