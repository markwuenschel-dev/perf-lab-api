---
status: proposed
date: 2026-06-20
---
# Hosting platform (Railway vs Render)

The app runs on **Render** today (live). `REDESIGN_ROADMAP.md` sketches a move to
**Railway** — managed Postgres (`config.py` already rewrites `DATABASE_URL` to
asyncpg), Cron Jobs for the P6 nightly pull, and a config-only cutover thanks to the
production Dockerfile.

Deferred to the P6 / ops boundary. **Leaning Railway** for the managed Postgres + cron
primitive, but not accepted: the migration only pays off once the wearable/cron work is
real, and the P0 app-boot bug must land first or any host boot-crashes identically.

**Guardrail:** keep the app host-agnostic (no host-specific code) so this stays a
config decision, not a code one.
