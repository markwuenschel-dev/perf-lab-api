---
status: superseded
date: 2026-06-20
decided: 2026-07-06
superseded: 2026-10-18
---
# Background-job scheduler for the nightly wearable pull

The P6 nightly wearable pull ([PDR-0006](../pdr/0006-wearable-sync-cloud-api-first.md))
needs a scheduler. Two options: the `[tasks]` extra (**Celery + Redis**), or a lighter
**platform Cron Job** (Render/Railway) hitting an internal sync endpoint.

**Decision (2026-07-06, accepted as the wearable layer shipped):** use a **Railway Cron
Job** that runs a one-shot command and exits — `python -m app.scripts.sync_wearables`.
The script opens its own `AsyncSessionLocal`, syncs every stored `WearableConnection`,
and terminates; no long-running worker, no Redis, no internal HTTP hop. This is the
lightest thing that works for a single nightly task and reuses the same Docker image as
the API service (see the deploy checklist in `docs/REDESIGN_ROADMAP.md`). Tied to
[ADR-0028](0028-hosting-platform.md) — since superseded: the app moved to a self-hosted
EC2 stack 2026-07-10 (see `docs/DEPLOY.md`).

**Guardrail (still in force):** do not add Celery/Redis to the default runtime; the
`[tasks]` dependencies stay optional. Revisit only if fan-out (many providers, retries,
backfills) outgrows a single cron command.

## Superseded (2026-10-18): no scheduler; wearable data is pulled when the athlete shows up

Nothing schedules the pull on EC2, and nothing will. The production box stops itself nightly
and after two idle hours (AWS-side controls, not configured in this repo), so a nightly job is
the one schedule sure to be missed. The web already syncs a stale connection
itself (`web/src/perflab/wearableSync.ts`: on app load and when the check-in opens, if the
last sync is more than 6 hours old). That on-demand catch-up is the scheduling policy.

- **What it does not give:** any unattended freshness guarantee. If nobody opens the app,
  nothing syncs, and state that wellness data feeds waits for the next visit. Days missed
  meanwhile are not lost: each sync starts from the day before `last_sync_at`.
- **Concurrency:** two syncs of one connection (app load and the check-in together, two
  workers, the CLI beside a user) are serialized by a per-connection advisory lock held for
  the whole sync (`app/services/wearable_sync_lock.py`). A caller waits up to 30 s for it
  (then 409); the sync itself has 60 s (then 504).
- **The CLI** (`python -m app.scripts.sync_wearables`) stays as a manual catch-up for an
  operator. It is not a backfill: once a connection has a `last_sync_at`, `--days` does not
  move its start date back. It exits 1 if any connection failed.
- The audit finding "no nightly sync scheduler" (plan v3) is resolved by this decision,
  not by building a scheduler. If the app moves to an always-on host, a scheduled run of
  the same CLI becomes worth revisiting; the lock already makes it safe beside users.
- The guardrail above stands: no Celery/Redis in the default runtime.
