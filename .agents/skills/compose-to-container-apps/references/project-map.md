# Uptime Monitor — Compose → Container Apps map

Source of truth: `docker-compose.yml`, `backend/Dockerfile`, `frontend/nginx.conf`,
and priority #3 in `AGENTS.md` ("small managed container architecture… managed PostgreSQL,
managed Redis… HTTPS, secrets, health probes, logs, documented scaling model"). `AGENTS.md`
also says: no Terraform or Kubernetes yet — use Bicep + Container Apps.

| Compose service | Azure target | Ingress | Scale | Notes |
|---|---|---|---|---|
| `frontend` (Nginx + React) | Container app `frontend` | **external**, HTTPS by ACA | 0–2 | Change `proxy_pass http://api:8000/status;` to `http://api/status;` (internal ingress listens on 80). Only `/api/status` is proxied — keep it that way. |
| `api` (FastAPI, port 8000) | Container app `api` | **internal only** | 1–3 | Management endpoints have no auth (see `AGENTS.md`). Never make this external until auth + rate limits exist. Probes: liveness `/health`, readiness `/health/db`. |
| `scheduler` | Container app `scheduler` | none | exactly 1 | Command `python -m app.scheduler`. Redis TTL claims already prevent double-enqueueing. |
| `worker` | Container app `worker` | none | 0–5 | Command `python -m app.worker`. KEDA `redis` scale rule on list `checks:queue`. Remember `BLPOP` can lose a job if a replica dies mid-check (known gap in `AGENTS.md`). |
| `migrate` | Container Apps job `migrate` | — | manual | Command `alembic -c alembic.ini upgrade head`. CI runs it before updating the apps. |
| `db` (postgres:17) | PostgreSQL Flexible Server 17 | private/firewalled | — | Entra auth via managed identity; see `python-azure-runtime`. Database `uptimemonitor`. |
| `redis` (redis:7) | Azure Managed Redis | — | — | `REDIS_URL` becomes `rediss://…:10000/0?socket_timeout=15`. |
| `webhook-receiver` | **not deployed** | — | — | Dev profile only. |

## Configuration

| Variable | Azure source |
|---|---|
| `DATABASE_URL` | Built from host/db/user env vars; token is the password (no secret stored) |
| `REDIS_URL` | Key Vault secret (if access-key auth) or host-only env var (if Entra auth) |
| `ALERT_WEBHOOK_URL` | Key Vault secret — treat as a credential; never log it |
| `ALERT_ALLOW_PRIVATE` | Plain env var, always `false` in Azure |
| `STATUS_CACHE_SECONDS` | Plain env var (default 15) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Env var from the App Insights resource |

## SSRF note

Workers make outbound requests to user-supplied URLs. `checker.py` blocks private, loopback,
link-local and metadata addresses — on Azure that includes `169.254.169.254` and the Container Apps
managed-identity endpoint. Keep those tests passing; add one for the identity endpoint
(`IDENTITY_ENDPOINT` env var) before the first deploy.
