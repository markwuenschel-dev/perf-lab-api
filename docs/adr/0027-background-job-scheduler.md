---
status: proposed
date: 2026-06-20
---
# Background-job scheduler for the nightly wearable pull

The P6 nightly wearable pull ([PDR-0006](../pdr/0006-wearable-sync-cloud-api-first.md))
needs a scheduler. Two options: the `[tasks]` extra (**Celery + Redis**), or a lighter
**platform Cron Job** (Render/Railway) hitting an internal sync endpoint.

Deferred to P6, decided by ops appetite. Leaning Cron Job — it avoids standing up Redis
and a worker for a single nightly task — but the call waits until the wearable layer is
actually being built. Tied to [ADR-0028](0028-hosting-platform.md) (the host provides
the cron primitive).

**Guardrail:** until accepted, do not add Celery/Redis to the default runtime; keep the
`[tasks]` dependencies optional.
