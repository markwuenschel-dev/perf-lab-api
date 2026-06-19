# Perf Lab — Multi-Domain Redesign Roadmap

> Status: direction settled 2026-06-19. This document supersedes the post-redesign
> portions of `docs/ROADMAP.md` (which still describes the pre-redesign frontend).
> It is the phased plan for wiring the redesigned `perf-lab-web` (`src/perflab/`)
> to `perf-lab-api` and building the engine out to match the product thesis.

## Context — why this work exists

`perf-lab-web` underwent a major redesign into a "Performance OS" shell
(`src/perflab/`: AppShell + Sidebar + 9 screens + 6 overlays). Most of it still
runs on a **client-side simulation** (`src/perflab/sim.ts`) and is being wired to
the real backend screen-by-screen. We needed to decide what the product *is*
before finishing the wiring, because the redesign was drawn running-only while the
backend is a general multi-domain engine.

**Product thesis (decided):** Perf Lab is a **concurrent / hybrid multi-domain
adaptive training engine** — one unified body state, all capacities at once, with
cross-talk. Domain is a *lens over one body*, not a mode switch. The athlete who
runs *and* lifts (Hyrox / CrossFit / tactical / runner-who-lifts) is the
first-class user. This is the shape the backend already has; the running-only UI
framing is what must broaden.

### The six spine decisions

1. **Multi-sport now**, not running-first. Broaden the UI; keep the general engine.
2. **Domain = lens over one body.** Concurrent training is first-class; Twin shows
   all axes always; readiness is whole-body and cross-talk-aware; blocks are hybrid.
3. **Benchmark system is THE measurement layer** (`/v1/benchmarks/*` +
   `/v1/dashboard/*`). The running-only Field Test (`/compute-metrics`) becomes one
   benchmark + onramp; it is retired as a separate source of truth.
4. **Objectives = new first-class model** — what the athlete trains *toward*
   (target benchmark + value + date + priority; many concurrent). Generalizes the
   orphaned Goal/Race screen.
5. **Readiness = one backend-owned scalar** = `f(modeled fatigue/tissue + acute
   daily wellness)`. Daily wellness (HRV/sleep/RHR/soreness/mood) is a first-class
   engine input.
6. **Wearable sync = cloud-API providers first** (Oura/Whoop/Polar) via server-side
   OAuth + nightly pull; manual entry is the universal fallback. Apple Health /
   Garmin deferred (web-only stack can't read HealthKit without a native shell).

Plus a cross-cutting engineering decision: **generate the web's TypeScript types
from the backend's OpenAPI schema** to end the hand-mirrored `types.ts` drift.

---

## Current state snapshot

**Wired (web `src/api/perfLabClient.ts` → api):** auth (register/token/me); Field
Test → `POST /compute-metrics`; Onboarding → `/v1/onboard`; Log Workout →
`/v1/simulate-dose` + `/v1/log-workout`.

**Built backend, NOT wired in the web:**
- `/v1/planning/*` (client fns exist; Planning screen not wired)
- `/v1/benchmarks/*` — definitions, observations (GET/POST), recompute-derived
- `/v1/dashboard/*` — kpis, domain-summary, readiness
- `/v1/weak-points` — GET / PATCH / DELETE (fully implemented)

**Missing backend the UI needs:** `GET /v1/state` (current `S(t)` only returns from
`log-workout`), state-history series, Objectives, Readiness scalar + daily-wellness
ingestion, wearable sync.

**Still local-only mock (`sim.ts`):** Simulator projection, Goal/Race prediction,
History trends, Session Player, morning Check-in.

### 🔴 Blocking bugs on `main` (P0 — fix first)

1. **App can't boot.** `app/logic/prescription_finalize.py` imports
   `encode_session_candidate` from the `app.logic.constraint_engine` package, but
   `__init__.py` only re-exports from `candidate.py` (singular) while the function
   lives in `candidates.py` (plural, line 70). Import chain
   `prescription_finalize → prescriber → planning → app.main` fails →
   **`main` is un-deployable** (Render is serving an older healthy `0.2.0` build;
   live `/ping` confirmed working) and **7 test files fail to collect**.
2. **`/openapi.json` 500.** `app/api/v1/prescribe.py:25` —
   `Query(TRAINING_GOAL_DEFAULT, description=...)`. The `...` is a literal Ellipsis
   that can't be serialized into OpenAPI →
   `PydanticSerializationError: Unable to serialize unknown type: <class 'ellipsis'>`.
   This blocks the OpenAPI→TS type-generation strategy.

Both have been latent since ~2026-05-29 (per `agent_collab` outbox notes).

---

## Phased roadmap

### P0 — Unblock the foundation (hours)

- **Fix Bug 1:** add `from app.logic.constraint_engine.candidates import
  encode_session_candidate` to `app/logic/constraint_engine/__init__.py` and its
  `__all__`. (Also note the duplication: `candidate.py` vs `candidates.py` — track
  a later consolidation, don't do it now.)
- **Fix Bug 2:** `prescribe.py:25` → replace `description=...` with a real string,
  e.g. `description="Training goal to prescribe for; defaults to the athlete's primary goal."`.
- **Verify:** `python -m pytest -q` collects all files; `python -c "from app.main
  import app; app.openapi()"` succeeds; `uvicorn app.main:app` boots.
- **Reconcile version drift** while here: `pyproject` 0.3.0 vs `app.main`/`/ping`
  0.2.0 vs web "v0.3" → pick one (0.3.0) and set `app.main` `version=` + `/ping`.

### P1 — OpenAPI-driven type contract

- Backend: confirm `/openapi.json` is clean for **all** routers (re-run the
  per-router isolation if anything else surfaces). Keep response models tidy.
- Web: add `openapi-typescript` devDep + an `npm run gen:types` script that writes
  `src/types.gen.ts` from the running/deployed `/openapi.json`. Make `types.gen.ts`
  the contract; migrate `src/types.ts` consumers; keep the hand-written client fns.
- Retire the manual-mirror ritual in both repos' `docs/SYNC_WITH_BACKEND.md`
  (replace with "regenerate + `tsc`").
- **Verify:** change a backend schema, regen, `tsc --noEmit` flags every break.

### P2 — Wire the built-but-dormant surface

- **Planning screen** (`PlanningScreen.tsx`) → existing `/v1/planning/*` client fns.
- **Benchmarks + Dashboard:** add client fns for `/v1/benchmarks/*` and
  `/v1/dashboard/*`; build a benchmark-observation entry surface and a KPI/readiness
  dashboard. Field Test screen → writes a benchmark observation (decision #3).
- **Weak points:** wire `/v1/weak-points` (GET/PATCH/DELETE) into a management surface.
- Resolve the **capacity-axis 5-vs-8** and **units (0–100 vs 0–1; sec/mile vs /km)**
  questions here (see Open Questions).
- **Verify:** end-to-end against a seeded DB (`python -m app.scripts.seed_benchmarks`).

### P3 — Current state + history endpoints

- New `app/api/v1/state.py` (mounted `/v1`): `GET /state` → latest
  `UnifiedStateVector` + readiness; `GET /state/history?days=N` → list from the
  append-only `athlete_states` table. Reuse `app.engine.state_bridge.unified_from_athlete_row`.
- Web: Twin/Overview load from `GET /state` instead of caching the `log-workout`
  response; History/time-travel consume `/state/history` (retire that `sim.ts` path).
- **Verify:** Twin renders real `S(t)` on cold load; history reflects logged sessions.

### P4 — Objectives model

- Backend: `app/models/objective.py` (user_id, `benchmark_code` FK→`benchmark_definitions.code`,
  target_value, target_date, priority, status, created_at); migration `a003_objectives`;
  `app/schemas/objective.py`; `app/services/objective_service.py` (CRUD + progress =
  latest observation vs target, direction-aware via `better_direction`);
  `app/api/v1/objectives.py`. Feed priority into the prescriber's stress allocation
  and a taper window near `target_date`.
- Web: Goal/Race screen → **Objectives** surface (multi-target, per-objective
  countdown + progress %).
- **Verify:** create objectives across domains; prescriber emphasis + countdowns reflect them.

### P5 — Readiness scalar + daily wellness

- Backend: `app/models/wellness.py` (`DailyCheckin`/`WellnessSample`: user_id, date,
  source, hrv_ms, sleep_hours, sleep_quality, resting_hr, soreness, mood, raw JSONB);
  migration `a004_wellness`; `app/services/readiness_service.py` computing one scalar
  `f(modeled F/T + acute wellness)` — reuse `overall_readiness`/`mean_fatigue`/
  `max_tissue_load` from `constraint_engine.candidate`. Add the scalar (+ drivers) to
  `ReadinessOut` and `GET /state`. Wire the prescriber readiness-redirect to consume it.
  `POST /v1/wellness/checkin` for manual entry.
- Web: Check-in overlay → real ingestion; one readiness number everywhere (retire the
  divergent `sim.ts` readiness formulas).
- **Verify:** a logged bad night lowers readiness and shifts the prescription.

### P6 — Wearable sync (cloud-API providers)

- Backend: new `app/integrations/` layer — provider adapters (`oura.py`, `whoop.py`,
  `polar.py`) normalizing HRV/sleep/RHR into `WellnessSample`; `app/models/wearable_connection.py`
  (encrypted tokens, scopes, last_sync_at); migration `a005_wearable`; OAuth
  connect/callback routes (`app/api/v1/integrations.py`); a nightly pull job.
  Scheduler options: the `[tasks]` extra (celery+redis) **or** a lighter Render Cron
  Job hitting an internal sync endpoint — decide by ops appetite (see Open Questions).
- Web: Settings → "Connect device" (OAuth) + sync status; manual entry stays.
- **Verify:** connect a sandbox account; nightly job writes `WellnessSample`s that move readiness.

---

## Consolidated feature list (goal #1)

**Implemented (backend) — keep / wire:** auth + onboarding + baseline state seeding;
workout loop (dose engine v0 → state update v0 → append-only `AthleteState`);
candidate prescriber (safety overrides, readiness redirects, scoring, finalization,
explainability); planning blocks + planned sessions + today; benchmark definitions/
observations/derived-KPI system (36 defs, 7 domains); dashboard KPIs/domain-summary/
readiness; weak-points CRUD; legacy field test (`/compute-metrics`).

**To build (new):** `GET /state` + history; Objectives; readiness scalar; daily
wellness/check-in ingestion; wearable-sync integration layer; OpenAPI type-gen.

**To wire (built, dormant in UI):** Planning, Benchmarks, Dashboard, Weak-points.

**Stays simulation for now (no backend planned yet):** Session Player (live guided
session), Simulator forward-projection — revisit if they become product-critical.

## Architecture assessment (goal #3)

**Healthy:** clean `api → service → logic → models` layering with thin routers;
append-only state history; Alembic-only schema management with a startup head-check;
disciplined deps (`pyproject` segregates `[llm]`/`[tasks]`/`[observability]` as
optional — the kitchen-sink impression is only the legacy `requirements.txt`); ruff
bans the deprecated `dose_engine`. The unified-body `S(t)` + cross-talk design is the
right fit for the concurrent product — no rework needed.

**Improve:** (a) the two P0 bugs + no CI gate caught a 3-week un-bootable `main` →
add a CI job that imports the app, generates OpenAPI, and runs pytest on every PR;
(b) kill manual type drift via P1; (c) `candidate.py`/`candidates.py` duplication →
consolidate; (d) delete legacy root `main.py` and `requirements.txt` once confirmed
unused; (e) the new wearable concern justifies a dedicated `app/integrations/` layer
rather than stuffing it into `services/`.

## Blind spots / risks (goal #4)

- **Un-bootable `main` masked by Render's last-good-deploy** — the live site is a
  stale build; the next deploy fails until P0 lands. No CI caught this.
- **Benchmark→state mappings cover only 13 of 36 definitions** — observations for the
  rest won't nudge `S(t)`. Audit `seed_benchmarks.py` mappings as P2/P5 work.
- **No Hyrox-specific benchmarks** despite Hyrox being a target persona (`mixed_modal`
  is the closest) — add definitions.
- **No CI / no green-suite gate**; 7 collection errors currently.
- **Field Test formula vs benchmark formula divergence:** legacy uses 300m+1.5mi;
  the seeded `run_fatigue_factor` uses 400m+1mile (Hinshaw). Reconcile when Field Test
  becomes a benchmark.
- **Token storage for wearables** needs encryption-at-rest + refresh handling.
- **DEV-only `user_id` override / auto-baseline** in `next-session` — confirm it's
  guarded for production.

## Hosting (Railway)

The app is **host-agnostic** — already Dockerized via the production Dockerfile, so
moving off Render is **config, not code**. **Prerequisite:** the P0 app-boot bug must
land first, or Railway boot-crashes identically to Render.

1. New Railway project + add the managed **PostgreSQL** plugin (it provides
   `DATABASE_URL` as `postgresql://…`, which `app/core/config.py` auto-rewrites to
   `postgresql+asyncpg://`).
2. Deploy the API service from the **production Dockerfile** (Railway auto-detects it).
3. Set env vars: `SECRET_KEY` (strong), `ALLOWED_ORIGINS` (the Netlify prod domain —
   the default `*.netlify.app` regex already covers deploy previews), `DEBUG=false`.
4. **Migrations run automatically** on deploy via the Dockerfile's `alembic upgrade head`.
5. **Seed once** after first deploy: `python -m app.scripts.seed_benchmarks` (and
   `seed_exercises`).
6. **Repoint the frontend:** set Netlify `VITE_API_BASE_URL` to the Railway URL —
   with **no** `/v1` suffix (the web client appends it) — and redeploy Netlify.
7. **Data:** `pg_dump` the old DB → restore into Railway Postgres **only** if there's
   real data worth keeping; otherwise schema (alembic) + reseed is enough.
8. **P6 nightly wearable pull** maps onto a Railway **Cron Job** (a service on a cron
   schedule that runs a command and exits) — no celery/redis required.

## Open design questions (resolve within their phase)

- **P2:** surface all 8 capacity axes or the UI's 5? Confirm `fatigue_f`/`tissue_t`
  scale (0–100 vs 0–1) and pace units (sec/mile vs /km).
- **P4:** exact prescriber consumption of objective priority (stress allocation vs
  taper-only).
- **P5:** how acute wellness combines with modeled fatigue (additive modifier vs cap/override).
- **P6:** scheduler — celery+redis (`[tasks]`) vs Render Cron Job; first provider
  (Oura vs Whoop) by actual user device mix.
- **Prescriber quality (cross-phase):** DB-driven exercise selection from the
  `Exercise` table (currently equipment-mapped fallback) — roadmap §5 of `ROADMAP.md`.
