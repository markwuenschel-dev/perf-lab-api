# Deploy runbook

Two pieces, two hosts:

- **Backend** (`/` — FastAPI + Postgres) → **Railway** (builds from `Dockerfile`, config in `railway.toml`).
- **Frontend** (`web/` — Vite SPA) → **Netlify** today (Cloudflare Pages optional later). Talks to the backend via `VITE_API_BASE_URL`.

The backend is the source of truth for the database; the frontend never touches Postgres directly.

---

## 1. Backend → Railway

1. **New project** → *Deploy from GitHub repo* → pick `markwuenschel-dev/perf-lab-api`.
   Railway auto-detects the `Dockerfile`; `railway.toml` adds the `/ping` health check.
2. **Add Postgres**: in the project, *New → Database → PostgreSQL*. Railway exposes it as a
   reference variable `${{Postgres.DATABASE_URL}}`.
3. **Set the API service Variables:**

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (reference — don't hardcode) |
   | `SECRET_KEY` | output of `openssl rand -hex 32` |
   | `ENVIRONMENT` | `production` |
   | `ALLOWED_ORIGINS` | your frontend URL, e.g. `https://joyful-liger-ef2f02.netlify.app` |
   | `ACCESS_TOKEN_EXPIRE_MINUTES` | `10080` (optional; this is the default) |

   `config.py` rewrites `postgresql://` → `postgresql+asyncpg://` automatically, so the
   raw Railway URL works as-is.
4. **Generate a domain**: service → *Settings → Networking → Generate Domain*. You get
   `https://<service>.up.railway.app`. Copy it.
5. **Verify**: open `https://<service>.up.railway.app/ping` → `{"status":"ok",...}`.
   Migrations run on every boot (`alembic upgrade head` in the Dockerfile `CMD`), so the
   schema is current. Docs live at `/docs`.

## 2. Frontend → Netlify

1. Netlify → site → *Site configuration → Environment variables*:
   `VITE_API_BASE_URL = https://<service>.up.railway.app` (no trailing slash).
2. *Deploys → Trigger deploy → Clear cache and deploy site* (Vite inlines the var at
   **build** time, so a redeploy is required).
3. Verify: load the site, sign in / register. The request should hit
   `…up.railway.app/auth/token`, not fail with "VITE_API_BASE_URL is not set".

## 3. Cloudflare (optional, later)

Adds a free global CDN + SSL + DDoS in front of either host:

- Add the domain to Cloudflare, switch nameservers, proxy (orange-cloud) the records.
- Either keep Netlify and CNAME a custom domain through Cloudflare, **or** move the
  frontend to **Cloudflare Pages** and drop Netlify. Backend stays on Railway either way;
  just keep `ALLOWED_ORIGINS` in sync with the final frontend origin.

---

## Local dev

- Backend + DB: `docker compose up` (Postgres + API on `:8000`), or run uvicorn against a
  local Postgres with `.env` (copy from `.env.example`).
- Frontend: `cd web && npm run dev`; copy `web/.env.example` → `web/.env.local` and point
  `VITE_API_BASE_URL` at `http://localhost:8000`.

## Notes

- **Migrations on multi-replica**: the Dockerfile runs `alembic upgrade head` on container
  start, which is fine for a single instance. If you scale to >1 replica, move the migration
  to a Railway *pre-deploy command* so replicas don't race.
- **Secrets**: never commit `.env`. Production secrets live only in the Railway/Netlify
  dashboards. Rotate `SECRET_KEY`, the DB password, and any PAT if they were ever shared.
