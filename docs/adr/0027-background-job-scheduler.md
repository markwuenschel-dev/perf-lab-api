---
status: accepted
date: 2026-06-20
decided: 2026-07-06
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
EC2 stack 2026-07-10 (see `docs/DEPLOY.md`). TODO: confirm how this cron job is now
scheduled on EC2, since Railway's managed Cron Job no longer exists.

**Guardrail (still in force):** do not add Celery/Redis to the default runtime; the
`[tasks]` dependencies stay optional. Revisit only if fan-out (many providers, retries,
backfills) outgrows a single cron command.
