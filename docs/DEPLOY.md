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
- **`TYPED_TOKENS_SINCE`** (required in production; boot fails without it, or if it is more
  than 24 h after boot). See *Typed-token cutover* below.

## Typed-token cutover (one time, the release that adds `TYPED_TOKENS_SINCE`)

An untyped (pre-`typ`) login token is accepted only if its `exp` ≤ `TYPED_TOKENS_SINCE` +
`ACCESS_TOKEN_EXPIRE_MINUTES`. So `TYPED_TOKENS_SINCE` must be **at or after the moment the
last untyped issuer stops** — the old container, which keeps serving logins until
`docker compose up -d` replaces it, *after* the image build. Set it to the deploy start and
anyone who logs in during the build gets a token that is rejected the moment the new
container boots.

Choosing a value later than the stop time costs nothing for real users (each untyped token
still dies at its own `exp`); it only widens the window in which a *stray* untyped issuer
would be trusted. So: a small margin, never an early value.

Because `T` must come after the old issuer stops but the stop time depends on how long the
build takes, do **not** use the one-shot deploy script for this release. Build first, then set
`T`, then swap — so `T` is chosen when the only remaining step is the seconds-long recreate:

1. On the box, sync the clone and build the image (the old container keeps serving):
   `cd /opt/stack/perf-lab-api && git fetch -q origin && git checkout -q main && git reset --hard origin/main`
   then, with the same build arg the deploy script passes (shadow rows record it):
   `BUILD_SHA=$(git rev-parse HEAD) && cd /opt/stack/infra && sudo docker compose build --build-arg APP_BUILD_SHA="$BUILD_SHA" perf-lab-api`.
2. Only after the build has finished, set `T` = now + 10 min, as ISO-8601 UTC:
   `date -u -d '+10 min' +%Y-%m-%dT%H:%M:%SZ`. Add `TYPED_TOKENS_SINCE=T` to
   `/opt/stack/infra/env/perf-lab-api.env`.
3. Replace the container immediately: `sudo docker compose up -d perf-lab-api`.
4. Verify the old issuer stopped before `T`:
   `sudo docker inspect -f '{{.State.StartedAt}}' $(sudo docker compose ps -q perf-lab-api)`
   must be **earlier than `T`** (the new container starts right after the old one stops), and
   `sudo docker compose exec -T perf-lab-api alembic current` must show the head.
   If `StartedAt` is not earlier (the swap was delayed), raise `T` to a time after `StartedAt`
   and run step 3 again. **Never lower `T`** — that rejects real tokens.
5. Leave `T` unchanged forever after. Once `T` + one token lifetime has passed, no untyped token
   can be valid and the legacy branch in `app/core/auth.py` can be deleted (tracked follow-up).
- **CRLF guard**: the deploy scripts strip `\r` on the remote side before bash reads the piped
  script — a CRLF checkout would otherwise make the box see `perf-lab-api\r` → "no such service".

## Missed sessions (P2): turning reconciliation on, and rolling it back

Migration `a052_missed_sessions` adds the `missed` session status and feedback supersession.
Nothing writes `missed` until `RECONCILE_MISSED_SESSIONS=true`, and it stays off by default.
Code that predates `missed` **cannot load a `missed` row**: its `SessionStatus` enum has no
such value. So the order is fixed:

1. Deploy the release with `a052` and the flag off. Every backend reader handles `missed`.
2. Deploy the web release that renders `missed`. In the same release or a later one, confirm
   that Planning, Week Review and the feedback flow show it.
3. Turn the flag on: add `RECONCILE_MISSED_SESSIONS=true` to
   `/opt/stack/infra/env/perf-lab-api.env`, then `sudo docker compose up -d perf-lab-api`.
   The first read of `/v1/planning/sessions`, `/today` or `/week-review` marks that athlete's
   past sessions: as of day D, anything dated before D−1 that is still pending.
   Reconciliation happens **only on those reads**. Writers don't reconcile, and they treat a
   stale pending row differently from a missed one: feedback is 409 before reconciliation and
   accepted after; a date-only move is accepted before and 409 after. Clients must act on the
   status a reconciling read showed them (`app/services/missed_session_service.py`).

**Rollback** (in this order: the old code must never see a `missed` row):

1. Set `RECONCILE_MISSED_SESSIONS=false` (or remove the line), then
   `sudo docker compose up -d perf-lab-api`.
2. Dry run, read the counts, then apply:
   ```bash
   sudo docker compose exec -T perf-lab-api python -m app.scripts.revert_missed_sessions
   sudo docker compose exec -T perf-lab-api python -m app.scripts.revert_missed_sessions --apply
   ```
   `--apply` refuses while the flag is on. Feedback given about a miss is superseded, not
   deleted.
3. Only if code from before P2 must run again: older code **will not boot** on an `a052`
   database. Its `alembic upgrade head` cannot find `a052`, and in production the boot
   check fails closed (`app/main.py` `_check_alembic_head`). So downgrade the schema first,
   **from the current image**, which is the only one that has `a052`'s downgrade:
   ```bash
   sudo docker compose exec -T perf-lab-api alembic downgrade a051_prescription_revisions
   ```
   then deploy the older SHA right away; the running container errors on feedback reads in
   between. The downgrade also turns any remaining `missed` into `pending`.

   The API doesn't need to be stopped for this. The downgrade first takes an `EXCLUSIVE` lock
   on `planned_sessions` and `session_feedback` and holds it until it commits:
   - Reads continue.
   - A session or feedback write already in progress is waited out, so the checks below see
     its result.
   - Writes that arrive later wait until the downgrade commits, then fail against the old
     schema; deploy the older SHA straight away.

   It **refuses, before changing anything**, in two cases:
   - **Any feedback row is superseded,** even a single one. Pre-P2 code has no supersession,
     so the row would become active again. The revert script in step 2 creates exactly these,
     and so does a late log or a reopen after feedback, even with the flag off.
   - **Any active feedback would stop describing its session,** such as feedback about a miss
     once the miss becomes `pending`.

   List them with:
   ```sql
   SELECT * FROM session_feedback WHERE superseded_at IS NOT NULL;
   SELECT sf.* FROM session_feedback sf JOIN planned_sessions ps ON ps.id = sf.planned_session_id
    WHERE sf.superseded_at IS NULL
      AND sf.describes_status IS DISTINCT FROM
          CASE ps.status::text WHEN 'missed' THEN 'pending' ELSE ps.status::text END;
   ```
   Export them, decide, and delete them before downgrading.
