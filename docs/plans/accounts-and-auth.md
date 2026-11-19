# Plan: accounts, authentication, and per-account monitors

Status: proposed. Nothing in this plan is implemented yet.
Related priority: `AGENTS.md` product priority #4 (authentication for management endpoints).

## Goal

Every monitor belongs to exactly one account. A signed-in user can create, see, check, and delete
only their own monitors, checks, and incidents. Requests without valid credentials are rejected.
Monitors stop being global.

## Current state (verified against the code)

- `backend/app/main.py` exposes every management endpoint without authentication.
  `GET /monitors`, `GET /incidents`, and every `/monitors/{id}/...` route operate on all rows.
- `backend/app/models.py` has `Monitor`, `Check`, and `Incident`. There is no user model. A monitor
  has no owner.
- `backend/app/statuspage.py` builds one global public status document (Redis key
  `statuspage:v1`) containing every monitor. `frontend/nginx.conf` proxies only `GET /api/status`
  to it.
- The scheduler and worker process all monitors, and the webhook in `ALERT_WEBHOOK_URL` is global
  and operator-owned. Its payload includes the monitor URL.
- The API is published on `127.0.0.1:8000`. Requests reach it directly, not through Nginx, so
  `X-Forwarded-For` cannot be trusted for client identity today.

## Design decisions

| Decision | Choice | Why |
|---|---|---|
| Credential type | Opaque random bearer tokens (`um_` + 32 bytes from `secrets.token_urlsafe`), sent as `Authorization: Bearer` | Revocable server-side with no signing-key management. Works with the FastAPI `/docs` Authorize button through `OAuth2PasswordBearer`. JWTs would need a revocation list to support logout. |
| Token storage | SHA-256 of the token in an `api_tokens` table, with `expires_at`, `last_used_at`, and `revoked_at` | A leaked database does not reveal usable tokens. A fast hash is fine because tokens have high entropy. |
| Password hashing | Argon2id via `argon2-cffi`, the only new runtime dependency | Current recommended algorithm. Hashing runs in synchronous endpoints, so it does not block the event loop. |
| Login identifier | Email, normalized to lowercase, unique | Simple, and normalization prevents duplicate accounts. |
| Registration | Disabled by default (`ALLOW_REGISTRATION=false`). The first account is created with a CLI command | Safe default for a self-hosted service whose workers fetch user-supplied URLs. |
| Access to others' data | Return 404, not 403 | Does not reveal which monitor IDs exist. |
| Ownership enforcement | Every query filters by `owner_id` in one shared dependency/helper, never ad hoc in each route | One place to get right and test. |
| Public status page | Per-account at `/s/{slug}`, backed by `GET /status/{slug}`. The global `GET /status` is removed | A global page would list every account's monitor names, which contradicts "monitors are not global". |
| Alerts | Keep the single operator webhook for now. Per-account webhooks are a follow-up | Keeps this change focused. The operator already sees every monitor. |

## Data model

New tables, added in migration `20261119_03`:

- **`users`**: `id`, `email` (String 254, unique index on `lower(email)`), `password_hash`,
  `status_slug` (String 40, unique, `^[a-z0-9-]{3,40}$`), `created_at`, `disabled_at` (nullable).
- **`api_tokens`**: `id`, `user_id` FK→users (`ON DELETE CASCADE`), `token_hash` (String 64,
  unique), `name` (String 100, e.g. "laptop"), `created_at`, `expires_at`, `last_used_at`,
  `revoked_at`. Index on `user_id`.

Changes to existing tables:

- **`monitors.owner_id`**: FK→users, indexed. It is added as nullable in `_03` and made
  `NOT NULL` in `_04` (see Rollout).
- `checks` and `incidents` keep no owner column. Ownership comes through `monitor_id` and is
  enforced by joining to `monitors`, which avoids two sources of truth.
- Deleting a user deletes their monitors, which cascades to checks and incidents through the
  existing ORM relationships plus a DB-level `ON DELETE CASCADE` on `monitors.owner_id`.

## API changes

New authentication endpoints:

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `POST` | `/auth/register` | none | 404 unless `ALLOW_REGISTRATION=true`. Body: email, password (12–128 chars), status_slug. 201 with the user. |
| `POST` | `/auth/token` | none | OAuth2 password form (`username`=email). Returns `{access_token, token_type, expires_at}`. Responds with the same 401 for an unknown email and for a wrong password, and verifies a dummy hash for unknown emails so response time doesn't reveal which emails exist. |
| `GET` | `/auth/me` | bearer | Current user (no password hash). |
| `DELETE` | `/auth/token` | bearer | Revokes the token used for the request (logout). |

Existing endpoints:

| Endpoint | Change |
|---|---|
| `POST /monitors` | Requires auth. Sets `owner_id` from the token. Enforces `MAX_MONITORS_PER_USER` (default 20), returning 409 when exceeded. |
| `GET /monitors` | Requires auth. Returns only the caller's monitors, ordered by ID. |
| `GET/DELETE /monitors/{id}`, `POST /monitors/{id}/check`, `GET /monitors/{id}/checks`, `GET /monitors/{id}/incidents` | Require auth. 404 unless the caller owns the monitor. |
| `GET /incidents` | Requires auth. Joins to `monitors` and filters by owner. |
| `GET /status` | Removed (404). Replaced by `GET /status/{slug}`, which is public and URL-free like today. |
| `GET /health`, `GET /health/db` | Unchanged and public. |

Authentication failures return `401` with `WWW-Authenticate: Bearer`. Disabled users and expired
or revoked tokens are treated the same as a missing token.

## Backend implementation

1. **`backend/app/models.py`**: add `User` and `ApiToken`; add `Monitor.owner_id` and the
   `owner` relationship.
2. **`backend/app/auth.py`** (new): `hash_password`, `verify_password`, `issue_token`,
   `hash_token`, and the `current_user` dependency. `current_user` looks up the token hash, checks
   expiry, revocation, and disabled status, and updates `last_used_at` at most once a minute to
   avoid a write on every request. Token lifetime comes from `TOKEN_TTL_HOURS` (default 168).
3. **`backend/app/main.py`**: add a `get_owned_monitor(monitor_id, user, db)` helper that all
   `/monitors/{id}` routes use, so ownership is checked in one place. Scope `query_incidents` by
   owner. Add the `/auth/*` routes.
4. **`backend/app/statuspage.py`**: `build_status(db, owner_id)`, with a per-account cache key
   `statuspage:v2:{owner_id}`. `invalidate(owner_id)` is called by `checks.record_check` (which
   knows the monitor) and by create/delete. Unknown slugs return 404 and are not cached.
5. **`backend/app/ratelimit.py`** (new): a Redis fixed-window counter (`INCR` + `EXPIRE`) using
   the existing `jobs.get_redis` seam. Limits:
   - `POST /auth/token`: 10 per 15 minutes per email and 30 per 15 minutes per client IP.
   - `POST /auth/register`: 5 per hour per IP.
   - `POST /monitors/{id}/check`: 6 per minute per user.

   Exceeding a limit returns `429` with `Retry-After`. If Redis is down, auth endpoints fail
   closed (503) and the manual check endpoint fails open. Client IP is `request.client.host`;
   honoring `X-Forwarded-For` waits for a configured trusted-proxy list (deployment work).
6. **`backend/app/cli.py`** (new): `python -m app.cli create-user --email … --slug …` prompts
   for the password; never pass it as an argument. `--claim-unowned` assigns all monitors with
   `owner_id IS NULL` to that user. Also `disable-user` and `revoke-tokens`.
7. **Scheduler, worker, alerts**: no behavior change. They are system processes and keep
   processing all monitors. `checks.record_check` passes `monitor.owner_id` to cache
   invalidation.
8. **Logging**: never log passwords, tokens, token hashes, or `Authorization` headers. Log
   authentication failures at INFO with the user ID when known, never the submitted email
   (a failed login is often a typo of a real address).

## Frontend and Nginx

- `frontend/nginx.conf`: replace `location = /api/status` with
  `location ~ ^/api/status/[a-z0-9-]{3,40}$`, proxying to `/status/{slug}`. Everything else under
  `/api/` stays 404, so `/auth/*` and the management routes remain unreachable through the public
  origin.
- `frontend/src/api.tsx` / `useStatus.ts`: read the slug from `location.pathname` (`/s/{slug}`)
  and fetch `/api/status/{slug}`. A 404 shows a "status page not found" state, distinct from
  "unavailable". The root path `/` shows a short "no status page selected" message.
- The account management UI is out of scope. Management stays in the API and `/docs`, as today.

## Rollout and migration of existing data

The demo database already has monitors without an owner, so ownership is added in two steps:

1. **PR 1 – schema and auth core.** Migration `_03` creates `users` and `api_tokens` and adds a
   nullable `monitors.owner_id`. It adds `auth.py`, `cli.py`, and the `/auth/*` routes. Management
   routes now require auth and filter by owner, so unowned monitors become invisible through the
   API but are still checked by the worker.
2. **Operator step.** Run
   `docker compose run --rm api python -m app.cli create-user --email … --slug … --claim-unowned`.
3. **PR 2 – enforce ownership.** Migration `_04` stops with a clear error message if any
   `owner_id IS NULL` rows remain, then sets the column to `NOT NULL`. Add rate limits and the
   monitor quota.
4. **PR 3 – per-account status page.** Make the status page per-account (backend, Nginx,
   frontend), and remove `GET /status`. This breaks the current public URL, so call it out in the
   PR and the README.
5. Update `README.md` (configuration table, API table, quick start: create a user first) and
   `AGENTS.md` (auth is implemented; the scoping rule is a convention) in the PR that changes each
   behavior.

New configuration: `ALLOW_REGISTRATION` (false), `TOKEN_TTL_HOURS` (168),
`MAX_MONITORS_PER_USER` (20). No new secrets are needed, because tokens are random rather than
signed.

## Tests

Add these to the existing SQLite + `fakeredis` setup. New `conftest.py` fixtures: `user_a`,
`user_b`, and `auth_client(user)`, which returns a `TestClient` with a bearer header.

- **Auth:** register (enabled and disabled), duplicate email with different case, password length
  bounds, login success, wrong password and unknown email give the same 401 body, expired token,
  revoked token, disabled user, logout revokes only the current token, the token hash (not the raw
  token) is stored.
- **Isolation (the core requirement):** for every `/monitors/{id}` route, user B gets 404 for user
  A's monitor. `GET /monitors` and `GET /incidents` never include another user's rows. Deleting
  user A's monitor as user B leaves it intact. Every management route returns 401 without a token.
- **Status page:** `/status/{slug}` shows only that account's monitors and no URLs. An unknown slug
  returns 404. Invalidation after a check clears only that account's cache key.
- **Rate limits and quota:** the 11th login attempt returns 429 with `Retry-After`. Creating
  monitor number `MAX+1` returns 409. Redis down gives 503 on login.
- **Migrations:** extend `test_schema.py`. `_03` upgrades a database that already has monitors.
  `_04` refuses to run while unowned monitors exist and succeeds after they are claimed.
- **CLI:** `create-user --claim-unowned` assigns only previously unowned monitors.
- **Frontend:** `api.test.ts` covers slug parsing, the 404 "not found" state, and keeping stale
  data on refresh errors.

## Out of scope / follow-ups

- Browser account management UI (login form, monitor CRUD). That needs a CSRF and session-cookie
  design of its own.
- Per-account webhook URLs. These need encrypted-at-rest storage and the existing alert SSRF
  checks.
- Teams, roles, or sharing monitors between accounts.
- Password reset and email verification. These need outbound email; until then the operator
  resets passwords through the CLI.
- MFA and OAuth/OIDC sign-in, which fit naturally with the Azure work (Entra ID).

## Risks

- **Forgotten filter:** a new route that queries `Monitor` directly would leak data across
  accounts. Mitigation: the shared `get_owned_monitor` helper, the isolation test matrix, and an
  `AGENTS.md` rule.
- **Breaking public URL:** `/api/status` disappears in PR 3. Mitigation: document it, and
  optionally keep `/` showing a "pick a status page" message.
- **Abuse via monitors:** any account can make workers fetch URLs. Existing SSRF protections stay.
  The per-user quota plus disabled-by-default registration bound the load.
