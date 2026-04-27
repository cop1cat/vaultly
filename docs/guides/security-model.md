# Security model

vaultly is a secrets manager — naturally, what it does (and doesn't)
protect against secret exposure is the most important thing to understand.

## What vaultly masks

| Operation                        | Masked? |
| -------------------------------- | ------- |
| `repr(model)`                    | ✅      |
| `str(model)`                     | ✅      |
| `model.model_dump()`             | ✅      |
| `model.model_dump_json()`        | ✅      |
| `model.model_dump(mode="json")`  | ✅      |
| `pydantic`'s rich repr / IPython | ✅ (via `__repr_args__`) |

Every secret field renders as `"***"` in all of these.

## What vaultly does NOT mask

### Direct attribute access

```python
print(config.db_password)   # prints the actual value
log.info("pw=%s", config.db_password)   # logs the actual value
```

A secret field is a plain `str` (or `int`, `dict`, etc.) — not a
`SecretStr` proxy. We chose this intentionally so downstream code (DB
drivers, HTTP clients) Just Works without adapters. The trade-off is
that **you** are responsible for not putting secret values into
log lines, formatted strings, or exception messages.

### Pickling / copying

These all raise `NotImplementedError`:

- `pickle.dumps(model)`
- `copy.copy(model)`
- `copy.deepcopy(model)`
- `model.model_copy()`

Why blocked: the in-memory cache holds cleartext secret values. Pickling
would write them to disk or the wire. `copy.copy` would silently *share*
the same cache between the source and the copy. `model_copy` would
duplicate it and break nested `_root` linkage.

If you need to reconstruct a model in another process or after a config
reload, ship the **constructor inputs** (the `stage="prod"` and
backend config) and reconstruct fresh on the other side.

### Process memory

Secret strings live in regular Python memory, not in any encrypted
buffer. They're not zeroed when evicted from the cache — Python's normal
GC eventually reclaims the memory but not immediately, and `mlock`-style
hardening would require a C extension we don't ship.

This means a process-memory dump (core file, `gcore`, debugger attach)
will reveal cached secrets. Mitigations live at the OS / orchestrator
level (no core dumps in prod, ptrace restrictions, etc.) — out of scope
for vaultly.

### Exception tracebacks

A traceback printed to stderr or logs from inside vaultly may include
the **resolved path** but never the **value**. We're explicit about this
in the error messages — but a `transform=` callable that raises a
`ValueError("bad value: <actual value>")` will, of course, leak that
value. Audit your `transform` callables accordingly.

### Logger output

vaultly uses the `vaultly` logger, which ships with a `NullHandler`
attached. Without explicit user configuration, vaultly's WARNINGs (retry
attempts, stale-on-error fallbacks) emit nothing.

When you do attach a handler, the WARNING records contain the resolved
path (e.g. `/prod/db/password`) but never the value. Resolved paths can
contain `{var}`-substituted context — tenant id, region, etc. — which
your compliance regime may classify as PII even without a value attached.

To filter / scrub before forwarding logs:

```python
import logging

class ScrubVaultlyPaths(logging.Filter):
    def filter(self, record):
        # transform record.msg / record.args here
        return True

logging.getLogger("vaultly").addFilter(ScrubVaultlyPaths())
```

## What vaultly does NOT promise

- It is **not** a cryptographic secret-handling library. It does not
  encrypt-at-rest, attest, or do constant-time comparisons.
- It does **not** prevent you from logging secret values. If `print(cfg.api_key)`
  ends up in stdout, that's on you, not vaultly.
- It does **not** rotate secrets — it consumes the rotated values that
  your secret store hands out. Rotation policy lives in SSM / Vault /
  whatever, not in vaultly.

## Defense-in-depth recommendations

- **Scope IAM / Vault policies** to the minimum your app needs. vaultly
  surfaces the path in errors — the path you're allowed to read should
  match the path you actually fetch.
- **Use SSM `SecureString`** (KMS-encrypted) or Vault for any value that
  matters. Never put credentials in plain `String` SSM parameters.
- **Don't pickle / cache to disk** any object that holds a `SecretModel`.
- **Set `stale_on_error=True` only after thinking through the risk** of
  serving a stale credential during an outage — sometimes failing is
  safer than continuing.
- **Disable core dumps** in production (`ulimit -c 0`, k8s
  `securityContext.allowPrivilegeEscalation: false`, etc.).
- **Restrict log access** like any other PII path.
