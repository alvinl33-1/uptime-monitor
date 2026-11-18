# Uptime Monitor status page

The public, read-only status page for Uptime Monitor, built with React 19, TypeScript, and Vite. See the [root README](../README.md) for the full system.

## What it does

- Fetches `GET /api/status` (same origin) and polls it every 30 seconds (`POLL_MS` in `src/useStatus.ts`). The API caches the status document for 15 seconds.
- Renders the overall state, summary metrics, monitor cards, and open incidents, plus loading, unavailable, and stale-data states. When a refresh fails, the last good data stays on screen.
- Never requests or displays monitor URLs. The public contract is `StatusResponse` in `src/types.ts`.

## Layout

| Path | Purpose |
|---|---|
| `src/App.tsx` | Page composition |
| `src/api.tsx` | Fetches and validates the status response; rejects malformed data and tolerates unknown overall states |
| `src/useStatus.ts` | Polling hook; aborts in-flight requests on cleanup |
| `src/types.ts` | TypeScript models for the status contract |
| `src/format.ts` | Shared formatting for counts, uptime, response time, and local timestamps |
| `src/index.css` | Design tokens, status colors, light/dark mode, responsive layout, reduced motion |
| `nginx.conf` | Production server on port 8080; proxies only `GET /api/status` to the API and returns 404 for other `/api/` paths |

## Commands

```bash
npm ci          # install exact locked dependencies
npm run dev     # Vite dev server
npm test        # Vitest
npm run lint    # Oxlint
npm run build   # type-check and production build to dist/
```

The Vite dev server has no `/api` proxy configured, so `npm run dev` on its own shows the unavailable state. To see live data, run the full stack with `docker compose up -d --build` from the repository root and open <http://127.0.0.1:3000>.

## Conventions

Keep the existing text-first visual style: no emojis, decorative icons, or component libraries. Reuse `format.ts` and the CSS variables, and maintain visible keyboard focus, readable contrast in both themes, and layouts that work at narrow widths.
