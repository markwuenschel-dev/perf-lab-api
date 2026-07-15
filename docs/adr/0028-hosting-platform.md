---
status: superseded
date: 2026-06-20
superseded_date: 2026-07-10
---
# Hosting platform (Railway vs Render)

> **Superseded 2026-07-10.** The Railway-vs-Render question below never resolved — the
> app instead moved to a self-hosted **AWS EC2 docker-compose stack**. That was an
> ops/infra decision made outside the Railway-vs-Render frame this ADR was scoped to, so
> no successor ADR number was minted; the live topology and deploy runbook are
> [`docs/DEPLOY.md`](../DEPLOY.md). Original reasoning preserved below for context.

The app runs on **Render** today (live). `REDESIGN_ROADMAP.md` sketches a move to
**Railway** — managed Postgres (`config.py` already rewrites `DATABASE_URL` to
asyncpg), Cron Jobs for the P6 nightly pull, and a config-only cutover thanks to the
production Dockerfile.

Deferred to the P6 / ops boundary. **Leaning Railway** for the managed Postgres + cron
primitive, but not accepted: the migration only pays off once the wearable/cron work is
real, and the P0 app-boot bug must land first or any host boot-crashes identically.

**Guardrail:** keep the app host-agnostic (no host-specific code) so this stays a
config decision, not a code one.
