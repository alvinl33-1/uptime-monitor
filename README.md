# Uptime Monitor

A self-hosted website monitoring project built with FastAPI, PostgreSQL, Redis, and React. It includes a monitor management API, a background check queue, incident tracking with webhook alerts, versioned database migrations, and a read-only public status page. This is a learning project and is not intended as a production monitoring service.

## Features

- Create, list, inspect, and delete monitors through the REST API.
- Check HTTP and HTTPS URLs on configurable intervals from 30 seconds to 1 hour.
- Record each check's status, HTTP status code, response time, error, and timestamp.
- Protect URL checks from requests to private, loopback, and cloud metadata addresses. Checks time out after five seconds and do not follow redirects or read response bodies.
- Queue checks in Redis and run them in independently scalable worker containers.
- Provide a cached, read-only status document and a React page that polls it every 30 seconds.
- Calculate 24-hour uptime and include current monitor states and open incidents in the status document.
- Open an incident after three consecutive failed checks and resolve it on the next successful check.
- Send webhook alerts when incidents open and resolve, and retry failed alerts every 60 seconds for up to 24 hours without delaying checks.
- Manage the PostgreSQL schema with Alembic migrations that run before the application services start.

> **Implementation note:** Scheduled and manual checks share one backend service that saves the check, evaluates incident transitions, invalidates the public status cache, and attempts the corresponding webhook alert. The scheduler retries failed alerts independently every 60 seconds within the existing 24-hour retry window.

## Architecture

```mermaid
flowchart LR
    Browser([Browser]) -->|:3000| Frontend[React status page / Nginx]
    Frontend -->|/api/status| API[FastAPI]
    User([API client]) -->|:8000| API
    Migrate[Migrate job] -->|alembic upgrade head| DB
    API --> DB[(PostgreSQL)]
    Scheduler[Scheduler] -->|due checks| DB
    Scheduler -->|enqueue| Redis[(Redis)]
    Scheduler -->|retry pending alerts| Webhook([Webhook endpoint])
    Redis --> Worker[Worker x N]
    Worker -->|safe HTTP check| Sites([Monitored sites])
    Worker -->|save check, incidents| DB
    Worker -->|incident alerts| Webhook
```

The migrate job, API, scheduler, and worker use the same backend image with different start commands. On `docker compose up`, the one-shot `migrate` service applies Alembic migrations after PostgreSQL is healthy, and the API, scheduler, and worker wait for it to finish successfully. The scheduler polls every five seconds and uses Redis keys to avoid enqueueing the same monitor repeatedly. Once a minute, it also retries webhook alerts that previously failed, in a separate step from check scheduling. Workers consume jobs from a Redis list, save results, update incidents, and send the immediate alert. The frontend's Nginx proxy exposes only `GET /api/status`; monitor management remains available directly through the API on port 8000.

## Quick start

Requirements: Docker with the Compose plugin, Git, and OpenSSL.

```bash
git clone https://github.com/alvinl33-1/uptime-monitor.git
cd uptime-monitor

# Create a local database password for Compose.
printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 16)" > .env

docker compose up -d --build
docker compose ps
```

Open the public status page at <http://127.0.0.1:3000> and the interactive API documentation at <http://127.0.0.1:8000/docs>.

Create a monitor in `/docs` with `POST /monitors`:

```json
{
  "name": "Example",
  "url": "https://example.com",
  "interval_seconds": 60
}
```

The interval must be between 30 and 3600 seconds. The scheduler will enqueue its first check on a subsequent pass. Follow service output with:

```bash
docker compose logs -f scheduler worker
```

## Services and ports

| Service | Purpose | Host access |
|---|---|---|
| `migrate` | Runs `alembic upgrade head`, then exits | One-shot |
| `frontend` | React status page served by Nginx | `http://127.0.0.1:3000` (published on all host interfaces) |
| `api` | FastAPI monitor, check, incident, health, and status endpoints | `http://127.0.0.1:8000` |
| `scheduler` | Enqueues due checks and retries pending webhook alerts | Internal |
| `worker` | Checks target URLs and saves results | Internal; can be scaled |
| `db` | PostgreSQL 17 data store | Internal |
| `redis` | Job queue and status cache | Internal |
| `webhook-receiver` | Development-only webhook test receiver | Dev profile only |

Scale workers with:

```bash
docker compose up -d --scale worker=3
docker compose logs -f worker
```

Stop the services while keeping database data, or remove the database volume too:

```bash
docker compose down
docker compose down -v
```

### Compose files

| File | When it applies | What it changes |
|---|---|---|
| `docker-compose.yml` | Always | Base services, health checks, networks, and resource limits. |
| `docker-compose.override.yml` | Loaded automatically by a plain `docker compose` command | Development settings: bind-mounts `./backend` into the API, scheduler, and worker so code changes don't require rebuilding the image. |
| `docker-compose.dev.yml` | Only when passed with `-f` | Extra definition for the development webhook receiver. |
| `docker-compose.prod.yml` | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` | Skips the development override and adds resource reservations. This is not a complete production deployment; see [Current limitations](#current-limitations). |

## Configuration

Compose reads these values from the project `.env` file or the shell environment:

| Variable | Default / requirement | Purpose |
|---|---|---|
| `POSTGRES_PASSWORD` | Required | Password for the Compose PostgreSQL user. |
| `ALERT_WEBHOOK_URL` | Empty | Webhook destination used for incident-opened and incident-resolved alerts. |
| `ALERT_ALLOW_PRIVATE` | `false` | Allows private webhook destinations when set to `true`; intended for local development. |

The backend also reads `DATABASE_URL` and `REDIS_URL`, which Compose sets for its services. The status cache reads `STATUS_CACHE_SECONDS` (default 15 seconds); it can be set in the backend service environment if needed. Do not commit `.env` or real webhook credentials.

## API overview

Interactive schemas and examples are available at `/docs`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | API process health |
| `GET` | `/health/db` | Database connectivity |
| `GET` | `/status` | Cached public status document; does not expose monitor URLs |
| `POST` | `/monitors` | Create a monitor |
| `GET` | `/monitors` | List monitors |
| `GET` | `/monitors/{id}` | Get one monitor |
| `DELETE` | `/monitors/{id}` | Delete a monitor and its checks and incidents |
| `POST` | `/monitors/{id}/check` | Run and save a check immediately |
| `GET` | `/monitors/{id}/checks` | List recent checks (`limit` 1–100) |
| `GET` | `/incidents` | List incidents (`open_only`, `limit`) |
| `GET` | `/monitors/{id}/incidents` | List incidents for one monitor |

The public status document contains the overall state, per-monitor state, 24-hour uptime, latest response time and check time, and currently open incidents. The frontend shows the overall state, summary metrics, a card for each monitor, and open incidents. It also has loading, unavailable, and stale-data states, and keeps the last good data when a refresh fails. It requests data through its same-origin `/api/status` route. The Nginx configuration returns 404 for other `/api/` paths.

## Development and tests

Backend tests use SQLite and fake Redis, so they do not need the Compose database or Redis services:

```bash
docker compose build api
docker compose run --rm --no-deps api pytest
```

`backend/requirements.in` contains the direct dependencies. Its fully resolved
`backend/requirements.txt` lock is used by local containers and CI so each
environment installs the same versions.

Frontend development and checks:

```bash
cd frontend
npm ci
npm run dev
npm test
npm run build
npm run lint
```

The development webhook receiver runs under the `dev` Compose profile:

```bash
docker compose --profile dev up -d webhook-receiver
docker compose logs -f webhook-receiver
```

It listens on port 9000 inside the Compose network and prints the payloads it receives. To send alerts to it locally, set `ALERT_WEBHOOK_URL=http://webhook-receiver:9000` and `ALERT_ALLOW_PRIVATE=true` in `.env`. To make it return HTTP 500 and exercise retries, create `/tmp/fail` inside its container.

## Database migrations

Alembic owns the database schema. Revisions live in `backend/alembic/versions/`. The Compose `migrate` service runs
`alembic upgrade head` after PostgreSQL is healthy and before the API,
scheduler, and worker start. For an existing database that was created before
migrations were added, first record that it already matches the initial schema:

```bash
docker compose run --rm migrate alembic -c alembic.ini stamp 20261116_01
```

Only stamp a database after confirming it matches the initial schema. Future
schema changes should add a reviewed Alembic revision, then run
`alembic upgrade head` as a release step before application containers start.

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`:

| Job | Checks |
|---|---|
| Backend tests | Python 3.14, `pytest`, and `pip-audit` against `backend/requirements.txt` |
| Frontend checks | Node 24, `npm audit --audit-level=high`, `npm test`, `npm run lint`, and `npm run build` |
| Build container images | Builds the backend and frontend images and fails on fixable HIGH or CRITICAL Trivy findings; runs after the other two jobs pass |

Third-party actions are pinned to commit SHAs, and the workflow has read-only repository permissions. CI does not deploy anything.

## Agent skills

The repository includes agent skills for the Azure deployment work. They live in one folder, `.agents/skills/`, and are linked for each tool:

- `.agents/skills/`: the real files (read by Codex)
- `.claude/skills`: symlink for Claude Code
- `.github/skills`: symlink for GitHub Copilot

Add, edit, or remove skills only in `.agents/skills/`. [`.agents/SKILLS.md`](.agents/SKILLS.md) lists each skill and when to use it. Project-level guidance for coding agents is in [`AGENTS.md`](AGENTS.md).

## Data model

- **Monitors:** name, URL, check interval, and creation time.
- **Checks:** monitor, `up`/`down` status, status code, response time, error, and check time.
- **Incidents:** monitor, start and resolution times, and alert-delivery timestamps.

Alembic migrations create and evolve the PostgreSQL schema. PostgreSQL data is stored in the `db-data` named volume. Redis has no persistent volume, so queued jobs and cached status are lost when Redis is recreated.

## Current limitations

- Webhook retries use a 24-hour retry window and fixed 60-second cadence; exponential backoff is not configured.
- A job can be lost if a worker stops after taking it from Redis; the next scheduled check eventually covers the gap.
- DNS rebinding is not fully prevented because the connection is not pinned to the validated IP address.
- The API has no authentication or rate limiting and should not be exposed publicly without additional protections.
- TLS, production secrets management, and database backups are not configured.

## Roadmap

- [x] PostgreSQL, Docker Compose, monitor API, and automated tests
- [x] Safe URL checker and Redis-backed scheduler/worker queue
- [x] Status API, Redis caching, and React status page
- [x] Incident evaluation and immediate webhook delivery integrated into scheduled and manual check processing
- [x] Run bounded webhook retry-window processing from the scheduler
- [x] Versioned schema migrations with Alembic
- [x] CI with GitHub Actions
- [ ] Add exponential backoff to webhook retries
- [ ] Reliable local workflow: health checks, useful logs, and documented manual verification
- [ ] Azure deployment target: Azure Container Apps with managed PostgreSQL and Redis, Key Vault secrets, HTTPS, health probes, and logs
- [ ] Deployment hardening: authentication for management endpoints, rate limits, backups, and observability

## Tech stack

Python 3.14, FastAPI, SQLAlchemy, Alembic, PostgreSQL 17, Redis 7, httpx, pytest, React 19, TypeScript, Vite, Vitest, Nginx, Docker, Docker Compose, and GitHub Actions.
