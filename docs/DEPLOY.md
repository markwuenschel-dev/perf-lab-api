# Deploy runbook

Prod is a multi-app **docker-compose stack on AWS EC2** (moved off Render/Railway, 2026-07-10).
Live: **https://perflab.44-198-76-44.nip.io** (nip.io wildcard DNS → EC2 `44.198.76.44`).

The app is host-agnostic (Dockerized) — nothing below is EC2-specific except the paths and the
`sudo docker compose` invocation. For migration mechanics see [DEPLOYMENT.md](DEPLOYMENT.md).

---

## TL;DR — one command

```powershell
./scripts/deploy.ps1                 # deploy latest main
./scripts/deploy.ps1 -Ref <sha>      # roll back / deploy a specific commit or tag
./scripts/deploy.ps1 -DryRun         # print the remote script, run nothing
```

```bash
./scripts/deploy.sh                  # deploy latest main
./scripts/deploy.sh <sha>            # roll back / deploy a specific commit or tag
DRY_RUN=1 ./scripts/deploy.sh        # print the remote script, run nothing
```

`deploy.ps1` (PowerShell) and `deploy.sh` (bash) are the same flow. They SSH to the box, advance
the build source, rebuild + restart the service, then verify with `alembic current` and a public
`/ping` check. Overridable knobs: `SshKey`/`SSH_KEY` (default `~/.ssh/shared-box.pem`),
`BoxHost`/`BOX`, `Url`/`URL`, `Tail`/`TAIL`.

---

## Topology (on EC2 host `ip-172-31-18-55`)

Two directories, one compose project (`stack`) at `/opt/stack/infra`:

- **`/opt/stack/perf-lab-api`** — a git checkout that is the **build source**. It must be advanced
  *before* rebuilding, or `docker compose build` re-bakes stale code (a silent no-op).
- **`/opt/stack/infra`** — the docker-compose stack. Build and run everything from here.

Services in the stack:

- **`caddy`** (`caddy:2-alpine`) — TLS + reverse proxy on `:80`/`:443`, routes to the apps.
- **`perf-lab-api`** — build context `../perf-lab-api`, Dockerfile target `backend-with-frontend`
  (embeds the SPA at `/static`, same-origin). Env: `/opt/stack/infra/env/perf-lab-api.env` — this
  sets `DATABASE_URL` to the shared Postgres. **The repo's own `.env` is irrelevant on the box.**
- **`postgres`** (`pgvector/pgvector:pg16`) — **shared** with neighbor apps (dominion-realm,
  leave-sprint, realmwalkers); perf-lab uses its own database within it.

## Manual deploy (what the scripts automate)

Run on the box with `sudo docker compose`:

1. **Advance the build source** (do this first — see topology):
   ```bash
   cd /opt/stack/perf-lab-api && git checkout main && git pull --ff-only
   git status   # confirm the checkout actually advanced before rebuilding
   ```
2. **Rebuild + restart** from the stack dir:
   ```bash
   cd /opt/stack/infra && sudo docker compose up -d --build perf-lab-api
   ```
3. **Migrations auto-run on boot** — `alembic upgrade head` runs before uvicorn in the image `CMD`
   (see [DEPLOYMENT.md](DEPLOYMENT.md)); no separate migration step.
4. **Verify** (wait a few seconds first — see gotcha):
   ```bash
   sudo docker compose exec perf-lab-api alembic current
   curl -fsS https://perflab.44-198-76-44.nip.io/ping
   ```

One-off scripts run in the container (so they get the shared-Postgres `DATABASE_URL`):
```bash
sudo docker compose exec perf-lab-api python -m app.scripts.<name>
```
Running these locally fails — the internal DB host isn't resolvable off the box.

### Rollback

`deploy.sh <sha>` / `deploy.ps1 -Ref <sha>` checks the ref out **detached**; deploying `main`
again restores normal operation (`git checkout main && git reset --hard origin/main`).

## Gotcha — the alembic race

After `up -d`, boot runs `alembic upgrade head` while uvicorn starts. Checking `alembic current`
in the first ~3–5 s can show the **old** head mid-migration — a race, not a failure. The deploy
scripts `sleep` before checking. If it still looks behind, tail the logs for `Running upgrade`.
Also always `git status`-verify the source advanced: a stale checkout builds fine but ships old
code **and** an old migration head.

---

## Local dev

- Backend + DB: `docker compose up` (Postgres + API on `:8000`), or run uvicorn against a local
  Postgres with `.env` (copy from `.env.example`).
- Frontend: `cd web && pnpm run dev`; copy `web/.env.example` → `web/.env.local` and point
  `VITE_API_BASE_URL` at `http://localhost:8000`.

## Notes

- **Secrets** live only in `/opt/stack/infra/env/perf-lab-api.env` on the box — never commit
  `.env`. In production set `ENVIRONMENT=production` and a real `SECRET_KEY` / `DATABASE_URL`;
  `config.py` rewrites `postgresql://` → `postgresql+asyncpg://` automatically.
- **CRLF guard**: the deploy scripts strip `\r` on the remote side before bash reads the piped
  script — a CRLF checkout would otherwise make the box see `perf-lab-api\r` → "no such service".
