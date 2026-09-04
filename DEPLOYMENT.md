# StockVerdict — Deployment Guide

Simplest reliable production layout:

```
Browser → CDN/static host (frontend/dist)  →  Python API (uvicorn)  →  SQLite/PostgreSQL
                                                          ↑
                              Background worker (python -m stock_alert_app.worker)
```

No Kubernetes, no WebSockets. Redis is optional (nothing requires it today;
sessions, notifications and caches live in the database / process memory).

---

## 1. Required services

| Service     | Required | Notes                                                        |
|-------------|----------|--------------------------------------------------------------|
| Python 3.13+| yes      | API + worker processes                                        |
| Node 20     | build    | Only to produce `frontend/dist` (CI does this)                |
| SQLite      | default  | Zero-config, file-backed; default `data/stock_verdict.db`     |
| PostgreSQL  | optional | See §3 — accepted as configuration; runtime support pending   |
| Redis       | no       | Reserved for future use; nothing depends on it                |

## 2. Environment variables

Copy `.env.example` → `.env` (never committed). Minimum for production:

```bash
STOCK_ALERT_ENV=production
STOCK_ALERT_AUTH_SECURE=1            # REQUIRED in production (HTTPS cookies)
STOCK_ALERT_DB=/var/lib/stockverdict/stock_verdict.db
HOST=127.0.0.1                       # bind behind a reverse proxy
PORT=8000
LOG_LEVEL=INFO
LOG_JSON=1
WORKER_MANAGED=1                     # API stops doing background news top-ups
```

Separate files per stage: `.env.development`, `.env.staging`, `.env.production`
(only `STOCK_ALERT_ENV`, `HOST`, secrets and cadences typically differ).
Provider credentials (`GEMINI_API_KEY`, `OPENCODE_API_KEY`, `REDDIT_CLIENT_*`,
`ALPHA_VANTAGE_KEY`, `NEWS_API_KEY`) are optional per capability — leave empty
and that capability degrades gracefully (NO_DATA), never crashes.
Frontend-only: `VITE_API_BASE_URL` (build time, no trailing slash) when the
built frontend is served from a different origin than the API. **Never put
provider secrets in frontend env vars.**

## 3. PostgreSQL setup

**Current status (honest):** the runtime is SQLite-native. `DATABASE_URL` is
accepted as configuration, and non-SQLite engines **fail fast at startup**
(`settings.validate_runtime()`) rather than half-work. The SQLite-specific
surfaces audited for the future dialect port: `INSERT OR REPLACE|IGNORE`
(→ `ON CONFLICT`), `?` placeholders (→ `%s`), `AUTOINCREMENT`, `PRAGMA`,
`executescript`, and `conn.total_changes` (one call site). Until the dialect
layer lands, run SQLite on persistent disk with regular backups (§11).
`schema_migrations` + the migration runner in `migrations.py` are already
engine-pluggable.

## 4. Migrations

Framework: `src/stock_alert_app/migrations.py` (append-only, recorded in the
`schema_migrations` table). Baseline is stamped automatically — fresh and legacy
databases converge safely; user/trading data is never dropped by migrations.

```bash
python -m stock_alert_app.migrate status    # show applied/pending
python -m stock_alert_app.migrate upgrade   # apply pending (idempotent)
```

`/api/health/ready` returns 503 while migrations are pending, so orchestrators
and smoke tests can gate on it. Legacy structures removed so far: only
`discovered_tickers` (data was migrated into `securities`). v1 paper tables
(`paper_portfolio`, `paper_orders`) are retained — dormant, not deleted.

## 5. Backend startup

```bash
pip install -e .                       # or: uv sync
set -a; source .env.production; set +a
python -m stock_alert_app.migrate upgrade
uvicorn stock_alert_app.web_app:app --host 127.0.0.1 --port 8000 --workers 2
# equivalent: stock-alert-app serve --host 127.0.0.1 --port 8000
```

Startup fails fast on: unsafe production cookie config, non-SQLite
`DATABASE_URL`, unwritable data dir. Build the frontend **before** starting if
you want the API to also serve the SPA (`frontend/dist`, or set `FRONTEND_DIST`).

## 6. Worker startup

```bash
set -a; source .env.production; set +a
WORKER_MANAGED=1 python -m stock_alert_app.worker
```

One worker process. Cadences: `STOCK_ALERT_REFRESH_FAST` (prices/technicals,
default 300s) and `STOCK_ALERT_REFRESH_SLOW` (LSTM/news/13F, default 1800s),
plus notification scans and committee-evaluation refresh each cycle. Every task
is isolated — a failing provider degrades only that task and is retried next
cycle. SIGTERM/SIGINT shut down cleanly between tasks.
Smoke/CI: `python -m stock_alert_app.worker --once`.

## 7. Frontend build/hosting

```bash
cd frontend
VITE_API_BASE_URL=https://api.example.com npm ci && npm run build   # CDN mode
# or same-origin (recommended): VITE_API_BASE_URL unset, proxy /api to uvicorn
```

* **Same origin (simplest):** serve `frontend/dist` from the API itself — the
  app already mounts `/assets` and an SPA fallback for deep links like
  `#/dossier/BSE%3APAYTM`. Path traversal is blocked (`..` → 404).
* **CDN/static host:** upload `dist/`, point `VITE_API_BASE_URL` at the API
  domain, and configure the host to rewrite all non-file paths to `index.html`
  (single-page app). CORS: keep same-site via reverse proxy when possible.

## 8. Domain + HTTPS

Terminate TLS at nginx/Caddy/load balancer, forward to `127.0.0.1:8000`:

```nginx
server {
  listen 443 ssl http2;
  server_name terminal.example.com;
  location /api/ { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; }
  location / { proxy_pass http://127.0.0.1:8000; }   # SPA + assets
}
```

`STOCK_ALERT_AUTH_SECURE=1` is mandatory here (session cookies get `Secure`).
HTTP→HTTPS redirect on.

## 9. Health checks

| Endpoint              | Meaning                                                         |
|-----------------------|-----------------------------------------------------------------|
| `GET /api/health`     | Liveness — process up (no dependencies touched)                 |
| `GET /api/health/ready` | Readiness — DB reachable **and** migrations applied (503 otherwise) |
| `GET /api/health/data` | Data health: providers, stale/NO_DATA/ERROR counts, coverage, worker queue |
| `GET /api/analytics/committee` | Committee performance (sample-sized buckets)          |

Load balancer: use `/api/health/ready`; container restart probes: `/api/health`.

## 10. Logging

`LOG_LEVEL=INFO`, `LOG_JSON=1` (structured JSON to stdout — ship with any
log collector). Every response carries `X-Request-ID`; unhandled errors return
`{"detail": "internal server error", "request_id": ...}` and log the traceback
with the same id. The worker logs one line per task with duration and counts.

## 11. Backups

SQLite: copy the DB file with the backup API or `sqlite3 ... ".backup ..."`
**after** `sqlite3 file "VACUUM INTO 'backup.db'"` (consistent snapshot):

```bash
0 * * * * sqlite3 /var/lib/stockverdict/stock_verdict.db "VACUUM INTO '/backups/sv-$(date +\%F).db'"
```

Retention: 7 daily + 4 weekly. Restore = stop services, replace file, run
`python -m stock_alert_app.migrate upgrade`, start services.

## 12. Rollback

1. Keep the previous frontend `dist/` artifact and previous backend image/tag.
2. Roll back frontend: re-point CDN/`FRONTEND_DIST` to the previous artifact.
3. Roll back backend: redeploy previous tag, then
   `python -m stock_alert_app.migrate upgrade` (migrations are append-only;
   they are forward-compatible with the previous release by policy — verify in
   staging before shipping destructive-capable migrations).
4. Database: restore latest backup **only** if a migration was destructive;
   otherwise forward-fix. Never hand-edit production rows.

---

## CI/CD minimum (`.github/workflows/ci.yml`)

* Backend: `pytest` + fresh-DB migration check (`migrate upgrade && status`)
* API smoke: boots uvicorn, curls `/api/health`, `/api/health/ready`,
  `/api/markets`, `/api/health/data`, `/api/analytics/committee`
* Frontend: production `vite build` (+ artifact upload)
* Worker smoke: `python -m stock_alert_app.worker --once`

## Secrets policy

`.env` is gitignored and untracked. Real keys live only in the platform secret
store. `.env.example` contains placeholders only. Provider keys are read
exclusively by the backend; frontend env vars must never contain secrets.
