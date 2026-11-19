# Plan: accounts, authentication, and a shared monitor catalog

Status: proposed. Nothing in this plan is implemented yet. This plan replaces the earlier
per-account ownership plan (`accounts-and-auth.md`).
Related priority: `AGENTS.md` product priority #4 (authentication for management endpoints).

## Goal

- Users sign in. Management endpoints require authentication.
- Monitors form one **shared, global catalog**, and each URL is monitored **at most once**.
  Duplicate monitors are rejected.
- A user **searches** the catalog. If the site is already there, they **add** that monitor to
  their list. If it isn't, they **register** (upload) a new monitor, which is added to their list.
- Each user's list ("my monitors") holds the catalog monitors they registered or added. Checks
  and incidents belong to the monitor, so everyone tracking the same site sees the same history.

## Current state (verified against the code)

- `backend/app/main.py` exposes every management endpoint without authentication.
  `POST /monitors` accepts any `http(s)` URL, so the same URL can be added any number of times.
- `backend/app/models.py` has `Monitor`, `Check`, and `Incident`. There are no users and no
  uniqueness on `monitors.url`.
- `backend/app/statuspage.py` builds one global public status document (`statuspage:v1`).
  `frontend/nginx.conf` proxies only `GET /api/status` to it.
- The scheduler and worker check every monitor once per interval. A shared catalog means one check
  per site, no matter how many users track it, which cuts the worker load that duplicates would
  cause today.
- The API is published on `127.0.0.1:8000` and reached directly, so `X-Forwarded-For` cannot be
  trusted yet.

## Design decisions

| Decision | Choice | Why |
|---|---|---|
| Monitor ownership | No owner. A monitor is a catalog entry with `created_by` (for audit and abuse handling) and a many-to-many `monitor_subscriptions` table linking it to users | Matches "global search, no duplicates": one monitor per site, many watchers. |
| Duplicate definition | Same **normalized URL** (rules below), enforced by a unique index on `url_hash` (SHA-256 of the normalized URL) | The database constraint closes the race between two users registering the same URL. Hashing keeps the index small regardless of URL length. |
| Register an existing URL | `POST /monitors` returns `409` with the existing monitor's ID. The client then calls `POST /monitors/{id}/subscribe` | Makes "search, then register only if missing" explicit, and the user is told it already exists. |
| Interval for shared monitors | Set by whoever registers the monitor. Not editable in v1 | Avoids conflicts between users who want different intervals. A follow-up can let it be the minimum of subscribers' preferences. |
| "Delete" | `DELETE /monitors/{id}/subscribe` removes the monitor from the caller's list. A monitor is deleted, with its checks and incidents, only when its last subscriber leaves | One user can't delete history others rely on. |
| Search scope | Any signed-in user can search all monitors by name, host, or URL | The requirement is a global search. |
| URL visibility | URLs are visible in the catalog to every signed-in user. URLs with credentials (`user:pass@`) are rejected, and the API docs warn that query strings are public | Shared monitors make URLs shared data, so secrets in URLs must be kept out. |
| Credentials | Opaque random bearer tokens, stored as SHA-256, with expiry and revocation. Argon2id passwords via `argon2-cffi` (the only new dependency) | Revocable and simple; works with the `/docs` Authorize button. Same choice as before. |
| Registration | `ALLOW_REGISTRATION=false` by default. The first user is created with a CLI command | Safe default for a service whose workers fetch user-supplied URLs. |
| Public status page | Per-user page at `/s/{slug}` showing the monitors on that user's list. The global `GET /status` is removed | A global page would list the whole catalog, growing without bound and exposing every user's choices. |

## URL normalization (the duplicate rule)

Implement `normalize_url(raw) -> str` in a new `backend/app/urls.py`. It runs on create and on
search, and is unit-tested heavily.

1. Parse with `urllib.parse.urlsplit`. The scheme must be `http` or `https` (as today).
2. Reject URLs that contain userinfo (`user:pass@host`).
3. Lowercase the scheme and host. Convert an internationalized host to punycode (IDNA) and strip a
   trailing dot.
4. Drop the default port (`:80` for http, `:443` for https). Keep any other port.
5. An empty path becomes `/`. Percent-encode non-ASCII path characters. Otherwise keep the path
   **case-sensitive** and unchanged, including any trailing slash.
6. Keep the query string exactly as written (order matters to some servers). Drop the
   `#fragment`, which is never sent to servers.
7. Treat `http` and `https` as different monitors, because they are different endpoints.

Examples: `HTTPS://Example.com:443` → `https://example.com/`, and `https://example.com/#top` →
`https://example.com/`. `https://www.example.com/` and `https://example.com/` are **not**
duplicates, and neither are `/a` and `/a/`. The API stores the URL as entered for display and
`url_normalized` for identity.

## Data model

Migration `20261119_03`:

- **`users`**: `id`, `email` (String 254, unique on `lower(email)`), `password_hash`,
  `status_slug` (String 40, unique, `^[a-z0-9-]{3,40}$`), `created_at`, `disabled_at`.
- **`api_tokens`**: `id`, `user_id` FK→users `ON DELETE CASCADE`, `token_hash` (String 64,
  unique), `name`, `created_at`, `expires_at`, `last_used_at`, `revoked_at`.
- **`monitor_subscriptions`**: `user_id` FK→users `ON DELETE CASCADE`, `monitor_id` FK→monitors
  `ON DELETE CASCADE`, `created_at`. The primary key is `(user_id, monitor_id)`, so a user can't
  add the same monitor twice. Index on `monitor_id`.
- **`monitors`** gains:
  - `url_normalized` (String 2000).
  - `url_hash` (String 64, **unique**).
  - `created_by` (FK→users, nullable, `ON DELETE SET NULL`, so a monitor outlives the account
    that created it).
  - `disabled_at` (nullable), so an operator can take down an abusive entry without deleting
    history.
- **Search index (PostgreSQL only):** enable `pg_trgm` and create GIN trigram indexes on
  `lower(name)` and `url_normalized`. The migration skips this on SQLite so tests still run.

Existing rows: `_03` backfills `url_normalized`/`url_hash` in Python using the same
`normalize_url`. If two existing rows normalize to the same URL, the migration **stops before
adding the unique index** and lists the conflicting IDs, so the operator can delete one through the
API or CLI and run it again. It never merges history silently. Existing monitors start with
`created_by = NULL` and no subscribers (see Rollout).

## API

Authentication (unchanged from the earlier plan):

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/auth/register` | 404 unless `ALLOW_REGISTRATION=true`. Email, password (12–128 chars), status slug. |
| `POST` | `/auth/token` | OAuth2 password form. Returns the same 401 for unknown emails and wrong passwords, and verifies a dummy hash so response time doesn't reveal which emails exist. Returns `{access_token, token_type, expires_at}`. |
| `GET` | `/auth/me` | Current user. |
| `DELETE` | `/auth/token` | Revoke the current token. |

Catalog and the user's list (all require a bearer token):

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/monitors/search?q=&limit=&cursor=` | Global search. `q` is 2–200 chars and is matched case-insensitively against name and normalized URL; `LIKE` wildcards are escaped. If `q` parses as an `http(s)` URL, an exact `url_hash` match is returned first as `exact_match`. Each result has `id, name, url, interval_seconds, subscriber_count, subscribed` (whether the caller has it). Disabled monitors are excluded. `limit` is 1–50 (default 20), ordered by name then ID, with a keyset cursor. |
| `POST` | `/monitors` | Register a new monitor: `{name, url, interval_seconds}`, same validation as today plus normalization. On success it is `201`, the caller is subscribed automatically, and `created_by` is set. If the URL exists, it is `409` with `{"detail": "Monitor already exists", "monitor_id": N}`. |
| `POST` | `/monitors/{id}/subscribe` | Add an existing monitor to the caller's list. Idempotent (`204`). `404` if the monitor doesn't exist or is disabled. |
| `DELETE` | `/monitors/{id}/subscribe` | Remove it from the caller's list (`204`). If no subscribers remain, the monitor and its history are deleted in the same transaction. |
| `GET` | `/monitors` | The caller's list (subscribed monitors), ordered by ID. |
| `GET` | `/monitors/{id}`, `/monitors/{id}/checks`, `/monitors/{id}/incidents` | Readable by any signed-in user, because the catalog is global. |
| `POST` | `/monitors/{id}/check` | Manual check. Only subscribers can run it, and it is rate limited, because it makes a real outbound request. |
| `GET` | `/incidents` | Incidents for monitors on the caller's list. |
| `DELETE` | `/monitors/{id}` | Removed. Replaced by unsubscribing. Operators use the CLI to disable or delete an entry. |
| `GET` | `/status/{slug}` | Public, URL-free status of that user's list. Replaces `GET /status`. |
| `GET` | `/health`, `/health/db` | Unchanged, public. |

Authentication failures are `401` with `WWW-Authenticate: Bearer`. A disabled user or an expired
or revoked token is treated as no token.

## Backend implementation

1. **`urls.py`** (new): `normalize_url`, `url_hash`. This module is pure, with no I/O.
2. **`models.py`**: `User`, `ApiToken`, `MonitorSubscription`; the new `Monitor` columns and a
   `subscribers` relationship.
3. **`auth.py`** (new): password hashing, token issue, hash and verify, and the `current_user`
   dependency. `last_used_at` is updated at most once a minute.
4. **`main.py`**:
   - **Create:** normalize, then insert the monitor and the creator's subscription in one
     transaction. Catch `IntegrityError` on `url_hash` and return 409 with the existing ID. Do not
     check first and then insert; the constraint is the source of truth, as with the incident
     index.
   - **Search:** one query with escaped `ILIKE` on `lower(name)` and `url_normalized`.
     `subscriber_count` comes from a grouped subquery, and `subscribed` from an `EXISTS` for the
     caller.
   - **Unsubscribe:** delete the subscription. Then
     `DELETE FROM monitors WHERE id = :id AND NOT EXISTS (subscription)`. This single statement
     avoids a race with a concurrent subscribe.
5. **`statuspage.py`**: `build_status(db, user_id)` over the user's subscriptions, with cache key
   `statuspage:v2:user:{user_id}`. A check on a shared monitor affects every subscriber's page, so
   `record_check` deletes the cache key of each of the monitor's subscribers. This is bounded,
   because results are cached for only 15 seconds.
6. **`ratelimit.py`** (new): a Redis fixed window through `jobs.get_redis`, returning 429 with
   `Retry-After`. Limits:
   - Login: 10 per 15 minutes per email, 30 per 15 minutes per IP.
   - Registration: 5 per hour per IP.
   - Search: 60 per minute per user.
   - Monitor creation: 10 per hour per user.
   - Manual checks: 6 per minute per user.

   Auth endpoints fail closed if Redis is down.
7. **Quotas:** `MAX_SUBSCRIPTIONS_PER_USER` (default 50).
8. **`cli.py`** (new):
   - `create-user --email --slug [--subscribe-existing]` prompts for the password and never takes
     it as an argument.
   - `disable-user`, `revoke-tokens`, `disable-monitor ID`, `delete-monitor ID`.
9. **Scheduler and worker:** they skip monitors with `disabled_at` set. Monitors with zero
   subscribers can't exist after `_04` (see Rollout). Before that, the scheduler skips them so
   unclaimed legacy monitors don't consume checks.
10. **Logging:** never log passwords, tokens, token hashes, `Authorization` headers, or search
    queries. Queries can contain URLs that users consider private.

## Frontend and Nginx

- `nginx.conf`: replace `location = /api/status` with
  `location ~ ^/api/status/[a-z0-9-]{3,40}$`. Everything else under `/api/` stays 404, so auth,
  search, and management stay off the public origin.
- `api.tsx` / `useStatus.ts`: take the slug from `/s/{slug}`, fetch `/api/status/{slug}`, and show
  a distinct "status page not found" state for a 404.
- Search and register in a browser UI are **out of scope** for this plan. The flow is used through
  the API and `/docs`. A browser UI needs its own cookie, session, and CSRF design.

## Rollout

1. **PR 1 – accounts and auth.** Add `users` and `api_tokens`, `auth.py`, `/auth/*`, and
   `cli.py create-user`. Management routes require a token. No catalog behavior changes yet.
2. **PR 2 – catalog and deduplication.** Add `urls.py`, migration `_03` (new monitor columns,
   subscriptions, unique `url_hash`, trigram indexes), search, subscribe/unsubscribe, the 409 on
   duplicates, and quotas and rate limits. Operator step:
   `python -m app.cli create-user … --subscribe-existing` so existing monitors have a subscriber.
3. **PR 3 – cleanup and status pages.** Migration `_04` stops with a clear message if any monitor
   has no subscribers, and documents `delete-monitor` for orphans. Add per-user status pages
   (backend, Nginx, frontend) and remove `GET /status` and `DELETE /monitors/{id}`. This breaks
   the public `/api/status` URL and the delete endpoint, so call it out in the PR and the README.
4. Update `README.md` (configuration, API table, a "find or register a monitor" walkthrough) and
   `AGENTS.md` (the catalog rule, "uniqueness is enforced by `url_hash`", and normalization) in the
   PR that changes each behavior.

New configuration: `ALLOW_REGISTRATION` (false), `TOKEN_TTL_HOURS` (168),
`MAX_SUBSCRIPTIONS_PER_USER` (50). No new secrets are needed.

## Tests

The existing SQLite + `fakeredis` setup plus new fixtures: `user_a`, `user_b`, and
`auth_client(user)`.

- **Normalization** (table-driven): case, default ports, IDNA, trailing dot, empty path, fragment,
  query preserved, userinfo rejected, `http` ≠ `https`, `/a` ≠ `/a/`, `www` ≠ apex.
- **Duplicates:** registering the same URL in a different form returns 409 with the original ID.
  Two concurrent creates produce one monitor (`IntegrityError` path). The duplicate attempt does
  not subscribe the caller.
- **Search:** matches by name, host, and partial URL. Exact-URL queries return `exact_match`.
  `%` and `_` are treated literally. Disabled monitors are hidden. Limit and cursor bounds work.
  `subscribed` and `subscriber_count` are correct for the caller. 401 without a token.
- **Lists:** `GET /monitors` shows only the caller's subscriptions. Subscribe is idempotent.
  Unsubscribing the last subscriber deletes the monitor and its history, while unsubscribing with
  others remaining keeps it. Only subscribers can run a manual check.
- **Auth:** the same matrix as before (login, enumeration-safe 401, expiry, revocation, disabled
  user, logout, hashes stored instead of raw tokens).
- **Status pages:** `/status/{slug}` contains only that user's list and no URLs. A check
  invalidates every subscriber's cache key.
- **Rate limits and quotas:** 429 with `Retry-After`. 409 past `MAX_SUBSCRIPTIONS_PER_USER`.
  Redis down gives 503 on login.
- **Migrations:** `_03` backfills hashes and stops with a list of conflicting IDs when duplicates
  exist. `_04` stops while orphans exist. Extend `test_schema.py`.
- **Frontend:** slug parsing, the not-found state, and stale data kept on refresh errors.

## Out of scope / follow-ups

- Browser UI for login, search, and register.
- Per-user alert webhooks (stored encrypted, using the existing SSRF checks). Until then, alerts
  go to the operator webhook.
- Editing a shared monitor's name or interval, or per-subscriber intervals.
- Detecting duplicates that differ only by redirect (`http`→`https`, `www`→apex). The checker
  doesn't follow redirects, so these stay separate monitors.
- Password reset, email verification, MFA, and OIDC/Entra ID sign-in.

## Risks

- **Secrets in shared URLs:** users may paste URLs that contain tokens in the query string, and
  every signed-in user can see them. Mitigation: reject userinfo, add a clear API docs warning,
  never log queries, and have the operator disable entries through the CLI.
- **Catalog spam:** mitigated by disabled-by-default registration, a creation rate limit, and the
  subscription quota. `created_by` identifies the source.
- **Normalization mistakes** either merge distinct sites or let duplicates through. Mitigation: a
  conservative rule set, a pure function with table tests, and a unique index as the final
  arbiter.
- **Breaking changes:** `/api/status` and `DELETE /monitors/{id}` go away in PR 3, so document both.
