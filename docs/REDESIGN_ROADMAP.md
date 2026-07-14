# Perf Lab — Multi-Domain Redesign Roadmap

> Status: direction settled 2026-06-19. This document supersedes the post-redesign
> portions of `docs/ROADMAP.md` (which still describes the pre-redesign frontend).
> It is the phased plan for wiring the redesigned `perf-lab-web` (`src/perflab/`)
> to `perf-lab-api` and building the engine out to match the product thesis.
>
> **Decisions now live as records.** The product decisions below are
> [`docs/pdr/`](pdr/); architecture decisions are [`docs/adr/`](adr/). This file keeps
> the *phasing, status, and how-to* and links the decisions out — it is no longer the
> home for the decisions themselves.

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

*Recorded as [PDR-0001](pdr/0001-concurrent-multidomain-thesis.md) (#1),
[PDR-0002](pdr/0002-domain-as-lens-over-one-body.md) (#2),
[PDR-0003](pdr/0003-benchmarks-are-the-measurement-layer.md) (#3),
[PDR-0004](pdr/0004-objectives-first-class.md) (#4),
[PDR-0005](pdr/0005-one-backend-owned-readiness-number.md) (#5),
[PDR-0006](pdr/0006-wearable-sync-cloud-api-first.md) (#6). The narrative stays here for
context.*

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

Plus a cross-cutting engineering decision ([ADR-0025](adr/0025-generate-ts-types-from-openapi.md)):
**generate the web's TypeScript types from the backend's OpenAPI schema** to end the
hand-mirrored `types.ts` drift (supersedes [ADR-0020](adr/0020-frontend-types-manual-mirror.md)).

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

### P6 — Wearable sync (cloud-API providers) — ✅ SHIPPED (Oura) 2026-07-06

Provider #1 (Oura) landed on `feat/wearable-sync-oura`. Both OAuth2 and Personal Access
Token paths, behind an adapter interface so Whoop/Polar are additive.

- Backend: `app/integrations/` layer — `base.py` (`WearableAdapter` protocol +
  `NormalizedWellness`) and `oura.py` (`OuraAdapter` over `httpx`, normalizing
  HRV/sleep/RHR into the canonical vocab); `app/models/wearable_connection.py`
  (Fernet-encrypted tokens, scope, `last_sync_at`); migration **`a018_wearable_connections`**;
  routes `app/api/v1/integrations_oura.py` (authorize / callback / connect-pat / sync /
  connection); `app/core/crypto.py` (Fernet at-rest); `app/services/wearable_service.py`
  (state token, token refresh, sync reusing `readiness_service.upsert_wellness_sample`).
- Scheduler: a **Railway Cron Job** runs `python -m app.scripts.sync_wearables` and exits
  ([ADR-0027](adr/0027-background-job-scheduler.md) accepted; no celery/redis).
- Config: `OURA_CLIENT_ID/SECRET`, `OURA_REDIRECT_URI`, `WEB_APP_URL`, `APP_ENCRYPTION_KEY`.
- Web: Settings → "Connect Oura" (OAuth) + PAT fallback + sync status + disconnect.
- **Verify:** connect via PAT (fastest) or OAuth; `POST /v1/integrations/oura/sync` or the
  cron writes `WellnessSample`s (`source="oura"`) that move `GET /v1/readiness`.
- Decisions: [ADR-0044](adr/0044-wearable-token-storage.md) (token storage + OAuth state),
  [PDR-0007](pdr/0007-first-wearable-provider.md) (Oura first).

---

### Wave 2 — Multi-Domain UX (direction settled 2026-07-07; PDR-0010, ADR-0045–0051)

Closes the running-centric UX gaps surfaced once the shell was in use: the backend is
already multi-domain; the `web/` frontend is still running-shaped and partly `sim.ts`-driven.
Dependency-ordered below. **Cross-cutting:** every phase that changes a schema re-runs
OpenAPI export → web `gen:types` → `tsc`, and the CI gate (import app + generate OpenAPI +
pytest). New prescriber control inputs compose with — and stay behind — the shadow EKF/MPC
([ADR-0041](adr/0041-shadow-ekf-state-covariance.md)/[ADR-0042](adr/0042-shadow-mpc-planner.md));
this wave does not promote them. The unifying spine across all of it: the *honesty ladder*
(measured / estimated / unknown, never fake) and *the model informs and self-limits — it
never blocks or overrules the user* ([PDR-0010](pdr/0010-model-self-limits-never-blocks-user.md)).

#### P7 — Foundation: schemas & contracts (the shared substrate)
- Migrations + models + Pydantic: `workout_set_logs` (session header → set rows,
  [ADR-0045](adr/0045-per-set-catalog-bound-workout-logging.md)); benchmark-definition
  enrichment cols `domain_lenses` / `movement_skill_mappings` / `assessable_skill_tags` /
  `measurement_protocol` ([ADR-0046](adr/0046-skill-state-domain-filtered-projection.md),
  [ADR-0047](adr/0047-one-benchmark-assessment-surface.md)); `planning_overrides`
  ([ADR-0051](adr/0051-user-owns-structure-engine-owns-safety.md)); `AthleteProfile`
  onboarding state-machine fields (`onboarding_status`, `completed_reason`,
  `initial_seed_status`, `initial_seed_confidence`) + a per-user tracked-signals preference
  ([ADR-0049](adr/0049-missing-wellness-is-a-gap-not-imputed.md),
  [PDR-0010](pdr/0010-model-self-limits-never-blocks-user.md)).
- Seed: the `run_vo2_field_test_300m_1p5mi` definition; skill/assessable-tag metadata on the
  existing skill benchmarks (`wl_technical_grade_85pct`, `gym_transition_quality`,
  `run_long_run_decoupling`, `run_threshold_talk_test`). Regenerate OpenAPI → web types.
- **Verify:** migrations up/down clean on a seeded DB; `gen:types` + `tsc` green; no route
  drops from `/openapi.json`.

#### P8 — Honest morning check-in ([ADR-0049](adr/0049-missing-wellness-is-a-gap-not-imputed.md)) — ✅ SHIPPED 2026-07-08 (re-scoped)
The modal→`ingestWellness` / `getReadiness` wiring the original bullet called for **already shipped
in P5** (endpoints are `POST /v1/wellness` + `GET /v1/readiness`; the `sim.ts` path is now the
signed-out fallback only). P8 was re-scoped to what was genuinely missing, adding
[ADR-0052](adr/0052-readiness-confidence-report-only.md) (confidence object, report-only gate) and
[ADR-0053](adr/0053-wellness-signal-registry.md) (registry, categories, implicit tracking, `stress`).
- **Three-state signals** (untracked / unknown-today / provided) via a canonical signal registry;
  profile-persisted explicit opt-out (`untracked_wellness_signals`) + implicit tracking; inline
  "I don't know today" / "I don't track this" + "add more signals" in the check-in modal.
- **Confidence** = a structured reliability object on `ReadinessScore` (`score`/`band`/`confidence`),
  evidence-coverage blend; **report-only** (`enforced=false`) — the *score* may nudge the plan
  (bounded ±0.15), *confidence* may not gate (that's P13). Shadow-logged for P13.
- **Freshness fix:** stale samples are no longer used as if fresh (no silent carry-forward); `stress`
  signal added (`a023`).
- **Verified:** a bad night lowers the score *and* shifts the prescription; an omitted-but-tracked
  signal lowers confidence but not the score; an untracked signal incurs no penalty; migrations
  up/down/up clean; 805 pytest passed; ruff + pyright clean; OpenAPI regenerated + web `tsc`/build green.

#### P9 — Per-set logging + strength loop ([ADR-0045](adr/0045-per-set-catalog-bound-workout-logging.md))
- Persist sets to `workout_set_logs`; catalog-bound log UI with `load_type`-typed fields +
  quick-entry expansion; heterogeneous session, derived modality.
- Write-time e1RM/benchmark extraction into `benchmark_observations` (measurement layer, not
  set-log scans).
- Strength prescription speaks in load: `%e1RM → suggested kg` (current e1RM + the
  [ADR-0029](adr/0029-periodization-intent-envelope.md) envelope) + RPE cap; seed carries it;
  dose uses actual load × RPE (closes [ADR-0039](adr/0039-dose-law-external-load-vs-effort.md));
  RPE-only fallback when no e1RM.
- **Verify:** log a mixed run+lift session — sets persist, a top set emits an e1RM
  observation, a prescribed squat pre-fills kg and its dose reflects external load.

#### P10 — One assessment surface + non-blocking onboarding ([ADR-0047](adr/0047-one-benchmark-assessment-surface.md) + [PDR-0010](pdr/0010-model-self-limits-never-blocks-user.md))
- `BenchmarkAssessmentSurface` (mode `onramp|retest`), domain-filtered; every submit writes a
  `benchmark_observation`; `/compute-metrics` demoted to the internal calculator behind the
  run VO₂ def; retire the standalone running Field Test.
- Onboarding: profile-basics (the only hard gate) → pick domains/objectives → per-domain
  recommended benchmarks → seed via [ADR-0035](adr/0035-benchmark-seeded-initial-state.md);
  explicit "I'm done for now" at every step + per-benchmark "do this later"; persisted
  completion; experience-prior fallback seed (low confidence, `estimated`); measurement-debt
  prompts in-app.
- **Verify:** a user who skips all benchmarks still reaches a usable, visibly-provisional
  twin; a strength user gets strength benchmarks, not a run test; reload doesn't re-trap.

#### P11 — Objectives drive training emphasis ([ADR-0050](adr/0050-objectives-drive-training-emphasis.md))
- Objective → weighted modality-vector blend (priority×proximity×gap×status), smoothed to a
  block-level `modality_mix`; multi-domain candidate generation from the mix; min-dose floors
  + phase logic; `primary_goal`/`block_goal` demoted to objective-less fallback.
- **Verify:** two cross-domain objectives produce a blended plan that shifts emphasis as
  dates/gaps change with no manual mode switch; objective-less users still get the fallback.

#### P12 — User overrides + authority stack ([ADR-0051](adr/0051-user-owns-structure-engine-owns-safety.md))
- `PlanningOverride` application in the pipeline: blend → floors → overrides →
  safety/confidence gates → candidates → optimize-within → tradeoff explanation; hard-override
  vs soft-preference; tradeoff-cost estimator; override UI ("use this structure / make it more
  efficient").
- **Verify:** a pinned hypertrophy block is honored, surfaces its objective cost, and is never
  silently re-optimized toward efficiency; an unsafe pin is modified/refused with an
  explanation.

#### P13 — Confidence-gated recommendations ([ADR-0048](adr/0048-confidence-gates-recommendations.md))
- Thread per-axis confidence ([ADR-0036](adr/0036-per-axis-confidence-scalar.md)) into the
  prescriber: continuous aggressiveness ceiling; discrete thresholds suppressing strong claims
  (race prediction, high-confidence tissue-risk). Distinct from safety overrides.
- **Verify:** a provisional athlete gets conservative progressions + suppressed strong claims;
  rising confidence restores them; safe-but-unmeasured is still trained, not blocked.

#### P14 — Skill-state projection ([ADR-0046](adr/0046-skill-state-domain-filtered-projection.md))
- `SkillView` projection service/endpoint: domain-filtered evidence over `capacity.skill` +
  open movement-keyed `skill_state` + skill benchmarks + weak-point tags; "not yet measured"
  only from assessable tags with a protocol; kill `sim.ts` `SKILL_DEFS`; rewrite the Twin
  skill card (demo mode explicit).
- **Verify:** the running skill card shows only evidence-backed items + honest "not yet
  measured"; a lifter sees lifting technique, not running economy; zero faked values.

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
  ✅ RESOLVED P6 — Fernet at-rest + OAuth refresh, see [ADR-0044](adr/0044-wearable-token-storage.md).
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
3. Set env vars: `SECRET_KEY` (strong), `ALLOWED_ORIGINS` (the explicit prod frontend
   origin, e.g. `https://perflab.44-198-76-44.nip.io` — required; boot fails without it,
   INT-09), `DEBUG=false`.
4. **Migrations run automatically** on deploy via the Dockerfile's `alembic upgrade head`.
5. **Seed once** after first deploy: `python -m app.scripts.seed_benchmarks` (and
   `seed_exercises`).
6. **Repoint the frontend:** set Netlify `VITE_API_BASE_URL` to the Railway URL —
   with **no** `/v1` suffix (the web client appends it) — and redeploy Netlify.
7. **Data:** `pg_dump` the old DB → restore into Railway Postgres **only** if there's
   real data worth keeping; otherwise schema (alembic) + reseed is enough.
8. **P6 nightly wearable pull** maps onto a Railway **Cron Job** (a service on a cron
   schedule that runs a command and exits) — no celery/redis required. Concretely: add a
   second service from the same repo/image (`buildTarget = "backend"`), override its start
   command to `python -m app.scripts.sync_wearables`, and set a cron schedule (e.g.
   `0 8 * * *`). Give it the same `DATABASE_URL` plus `APP_ENCRYPTION_KEY` and `OURA_*`
   vars. Do **not** run `alembic upgrade head` there — the API service already migrates on
   deploy.

## Decision records & tracked cleanups

This pass moved decisions into [`docs/adr/`](adr/) + [`docs/pdr/`](pdr/) and resolved
two long-open calls:

- **Accepted:** [ADR-0023](adr/0023-eight-capacity-axes-everywhere.md) — eight capacity
  axes everywhere (engine→UI, no rollup); [ADR-0024](adr/0024-canonical-units-imperial-pace.md)
  — pace stored as sec/mile, fatigue/tissue on 0–100.
- **Proposed (revisit in-phase):** readiness combine rule
  [ADR-0026](adr/0026-readiness-combine-rule.md) (P5) · scheduler
  [ADR-0027](adr/0027-background-job-scheduler.md) (P6) · hosting
  [ADR-0028](adr/0028-hosting-platform.md) (lean Railway, P6) · first wearable provider
  [PDR-0007](pdr/0007-first-wearable-provider.md) (P6).

**Tracked cleanups (tasks, not ADRs — they don't clear the "real trade-off" bar):**

- [ ] Add a CI gate (import app + generate OpenAPI + run pytest on every PR) — would
  have caught the ~3-week un-bootable `main`.
- [ ] Reconcile version drift to **0.3.0** (`pyproject` / `app.main` / `/ping`).
- [ ] Delete legacy root `main.py` + `requirements.txt` once the legacy router is
  confirmed to cover `/compute-metrics`.
- [ ] Consolidate `constraint_engine/candidate.py` ↔ `candidates.py` duplication.

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
- **Wave 2 (P11–P13):** objective proximity/gap curve shapes + smoothing ρ (per-objective-type
  urgency curves); the confidence aggressiveness-ceiling curve + discrete claim thresholds; and
  the tradeoff-cost estimator's model (how "adds ~3 weeks to your squat objective" is computed).
  Deferred to their phases — the ADRs fix the *shape*, not the constants.
