---
name: python-azure-runtime
description: Code changes Uptime Monitor (FastAPI / SQLAlchemy / psycopg / Redis) needs to run on Azure Container Apps — passwordless PostgreSQL Flexible Server via managed identity, Azure Managed Redis over TLS, Key Vault-backed secrets, and Application Insights telemetry.
---

# Running Uptime Monitor on Azure

Pair with `compose-to-container-apps` (infrastructure), `azure-identity-py` (credentials) and
`azure-monitor-opentelemetry-py` (telemetry details). Follow `AGENTS.md`: synchronous SQLAlchemy,
module-level seams for tests, no new framework. Every change must keep the Compose stack and the
SQLite/fakeredis tests working — enable Azure behaviour from env vars only.

## 1. Passwordless PostgreSQL

`backend/app/db.py` currently builds the engine from `DATABASE_URL`. Add an Entra mode that injects a
fresh token as the password for each new connection:

```python
import os
from azure.identity import DefaultAzureCredential
from sqlalchemy import create_engine, event

PG_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

def make_engine():
    url = os.getenv("DATABASE_URL", "")
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=1800)
    if os.getenv("DATABASE_USE_ENTRA", "").lower() == "true":
        credential = DefaultAzureCredential()   # picks up the Container Apps managed identity

        @event.listens_for(engine, "do_connect")
        def _inject_token(dialect, conn_rec, cargs, cparams):
            cparams["password"] = credential.get_token(PG_SCOPE).token
    return engine
```

- Azure `DATABASE_URL`: `postgresql+psycopg://<identity-name>@<server>.postgres.database.azure.com:5432/uptimemonitor?sslmode=require` (no password).
- Tokens last about an hour; `pool_recycle` below that keeps pooled connections from outliving them.
  `azure-identity` caches tokens, so `get_token` per connection is cheap.
- Alembic `env.py` must use the same `make_engine()` so the migration job authenticates the same way.
- One-time DB setup by the Entra admin:
  `select * from pgaadauth_create_principal('<identity-name>', false, false);` then grant schema rights.
- Add `azure-identity` to `requirements.in`, regenerate `requirements.txt`; CI's `pip-audit` covers it.
- Test: monkeypatch the credential and assert the listener sets `password`; keep it out of the
  SQLite path.

## 2. Azure Managed Redis

- TLS only: `REDIS_URL=rediss://<name>.<region>.redis.azure.net:10000/0?socket_timeout=15`
  (confirm host/port on the resource's overview page).
- Start with **access-key auth**: store the full URL (with key) as a Key Vault secret, mapped to a
  container app secret and then to `REDIS_URL`. Zero code changes.
- Later, move to **Entra auth** with the `redis-entraid` credential provider so no key exists at all;
  check that package's README for the current API, wire it into `jobs.get_redis()`, and keep the
  `fakeredis` seam for tests.

## 3. Secrets

| Secret | Where it lives |
|---|---|
| `ALERT_WEBHOOK_URL` | Key Vault → container app secret → env var (scheduler + worker only) |
| Redis URL with key | Key Vault (until Entra auth) |
| DB password | none — token auth |

The existing rule holds: logs never contain full webhook URLs or tokens.

## 4. Application Insights

```python
# backend/app/telemetry.py
import os

def configure_telemetry(service_name: str) -> None:
    if not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return
    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor()
```

- Call it at the top of each entry point with its own name: `api` (in `main.py` before the app is
  created), `scheduler`, `worker`. Different names give separate nodes in the application map.
- Add a span around each check in `checks.py` with monitor ID, status and duration. **Never** record
  the monitor URL or webhook URL as an attribute — it is public-status-sensitive data per `AGENTS.md`.
- HTTP calls made by `checker.py` through `httpx` show up as dependencies; that is useful, but check
  the exported attributes don't include full target URLs. If they do, disable the httpx
  instrumentation and record your own sanitized span.

## 5. Health and SSRF on Azure

- Probes: liveness `/health`, readiness `/health/db`. Scheduler and worker have no HTTP port — rely on
  restart-on-exit, and make both loops exit non-zero after repeated Redis/DB failures rather than
  spinning forever.
- Container Apps exposes a managed-identity endpoint to every container (`IDENTITY_ENDPOINT`). Make
  sure `checker.py` and the webhook SSRF checks reject it, and add a test before the first deploy.
