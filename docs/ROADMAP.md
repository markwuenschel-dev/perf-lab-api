# Performance Lab Roadmap

## Purpose

This roadmap captures the current project position and what should come next.

It answers:

1. what is already present
2. what has moved from planned to implemented
3. what remains to make the system complete

This is not a promise of dates. It is a map of architectural priorities.

## Current Position

Performance Lab now has the shape of a real adaptive training engine:

- FastAPI backend
- JWT auth
- Alembic-managed PostgreSQL schema
- unified state model `S(t)`
- stress dose layer `D(t)`
- workout-to-state service loop
- planning blocks and planned sessions
- candidate-based prescription engine
- weak-point signal layer
- benchmark observation and KPI layer
- React/TypeScript frontend control console
- onboarding and planning UI surfaces

The strongest foundation is the separation between event history, state history, planning, benchmark observations, weak points, and prescriptions.

## Roadmap Themes

1. Core engine hardening
2. Onboarding/profile completeness
3. Planning and calendar maturity
4. Benchmark/KPI assimilation
5. Prescriber and exercise selection quality
6. Frontend product workflow
7. Testing and production hardening

## 1. Core Engine Hardening

### 1.1 Alembic migrations

Status: complete.

Current migrations:

- `a000_init` — foundational tables
- `a001_benchmark_kpi` — benchmark/KPI tables and observation mappings
- `a002_planned_bench_cols` — planned-session benchmark columns

App startup checks the Alembic head: on a confirmed mismatch it fails fast
(raises) when `ENVIRONMENT=production`, and logs loudly otherwise. The migration
workflow is documented in `docs/DEPLOYMENT.md`.

### 1.2 State-vector bridge

Status: complete.

`engine_state` JSONB stores decomposed vectors while legacy scalar columns remain available. Bridge helpers convert both directions. Each `engine_state` payload is stamped with `ENGINE_STATE_SCHEMA_VERSION`, and historical rows migrate lazily on read via `_migrate_engine_state` (keyed on the stored version) — so evolving the vector schema needs no Alembic migration.

### 1.3 Deprecated module cleanup

Status: complete.

Both deprecated modules have been removed:

- `app.logic.dose_engine` — deleted (had no remaining importers)
- `app.logic.state_update` — removed in an earlier pass

Preferred:

- `app.logic.dose_engine_v0`
- `app.logic.state_update_v0`
- `app.services.state_service`

Re-introduction is guarded: ruff's `flake8-tidy-imports` banned-api fails the
lint gate (`TID251`) on any import of `app.logic.dose_engine` or
`app.logic.state_update`.

## 2. Onboarding and Athlete Setup

### 2.1 Register + profile shell

Status: implemented.

`POST /auth/register` creates both `User` and an empty `AthleteProfile`.

### 2.2 Onboarding endpoint

Status: implemented.

`POST /v1/onboard` fills the profile, creates self-reported weak points, and seeds baseline state. All baseline request fields the schema accepts — deadlift, bench, bodyweight, and run-5K — are now persisted onto the profile.

### 2.3 Baseline state seeding

Status: complete.

Four-tier experience-level baseline table, with squat 1RM override for force
capacity. `habit_strength` is seeded from experience years, per-lift
`skill_state` from experience level (bumped where a 1RM is supplied), and aerobic
capacity from the onboarding 5K time.

Future enhancement: refresh `habit_strength` from adherence history during
logging (only experience years is available at onboard time).

## 3. Planning and Calendar

### 3.1 Block creation and planned sessions

Status: implemented.

`POST /v1/planning/blocks` creates a block and generated planned sessions.

Current behavior:

- default templates for every `BlockGoal` (all nine goals carry real weekly templates)
- Strength fallback for any goal without a template
- deload weeks by cadence; `deload_volume_factor` scales the deload session's prescribed duration
- periodic benchmark sessions by cadence
- `PATCH /v1/planning/blocks` edits status, rationale, `modality_mix`, and `deload_volume_factor`

Remaining targets:

- let the frontend define/edit custom weekly templates (the API already accepts a custom `weekly_template`; the editing UI is a frontend task — see §6.2)

### 3.2 Planned session management

Status: backend complete; some frontend views remain.

The API lists sessions, updates status, reschedules dates, and retrieves today's
pending session with generated prescription content. Rescheduling now preserves
the original plan date (`original_scheduled_date`, migration `a003`) and marks a
moved session `RESCHEDULED`; repeated skips in the active block bias the
prescriber toward lighter/variety/recovery work (annotated
`adherence:recent_skips=N`).

Frontend has a `PlanningPanel` for block creation, block list, session list,
complete/skip/reschedule actions.

Remaining targets (frontend):

- richer calendar view
- planned vs completed comparison view

## 4. Benchmark and KPI Assimilation

### 4.1 Benchmark definitions and observations

Status: implemented.

The API supports listing definitions and posting observations.

### 4.2 Observation mappings into state

Status: implemented as weighted residual-style nudges.

Valid benchmark observations can create new `AthleteState` rows using observation mappings.

Remaining targets:

- stronger normalization rules per benchmark definition
- retest interval enforcement or warnings
- richer timestamp ordering safeguards for benchmark-driven state rows

### 4.3 Weak-point feedback from benchmarks

Status: implemented.

Current thresholds:

- normalized value `< 40` flags benchmark weak points
- normalized value `> 65` resolves matching benchmark weak points

Remaining targets:

- calibrate thresholds by domain
- aggregate multiple weak-point sources instead of passing raw active tags
- expose standalone weak-point management routes

### 4.4 Derived KPI snapshots

Status: implemented.

Derived metric formulas support sum, ratio, weighted_sum, and custom functions.

Remaining targets:

- frontend dashboard views for KPI history
- confidence and staleness display
- domain-specific KPI interpretations

## 5. Prescriber Enrichment

### 5.1 Candidate-based controller

Status: implemented.

The prescriber builds a candidate pool, applies safety overrides/readiness redirects, scores candidates, and finalizes with explainability.

### 5.2 Safety and readiness behavior

Status: implemented.

Hard safety overrides and soft readiness redirects are present.

Remaining targets:

- tune thresholds with scenario tests
- document expected behavior per fatigue/tissue channel
- avoid over-conservatism under mixed signals

### 5.3 Block context

Status: partially implemented.

A +0.15 score boost is applied when candidate type matches planned session category. Deload/benchmark flags are annotated in explanation, and the block's `deload_volume_factor` now scales a deload session's prescribed duration.

Remaining targets:

- use block templates to shape candidate generation, not just scoring
- extend deload scaling beyond duration to volume/intensity targets
- make benchmark sessions prescribe benchmark-specific content

### 5.4 Exercise selection

Status: MVP fallback implemented.

The prescriber currently returns equipment-mapped exercises with bodyweight fallback.

Remaining targets:

- select exercises from the `Exercise` database table
- filter by equipment_required, skill demand, impact, weak-point tags, and sport domain
- include coaching notes and scalable-by guidance in prescriptions
- improve per-exercise dose feedback loop

### 5.5 Provenance and structured templates

Status: implemented at the finalization layer, depending on settings and registry data.

Remaining targets:

- complete template registry coverage across goals
- expose provenance cleanly in UI
- test hard/soft constraint behavior by domain

## 6. Frontend Product Workflow

### 6.1 Core twin loop

Status: implemented.

Frontend supports:

- auth
- onboarding gate
- goal selection
- next-session retrieval
- dose simulation
- workout logging
- state display
- automatic prescription refresh

### 6.2 Planning panel

Status: MVP implemented.

Frontend supports:

- block creation
- block list
- session list over date window
- complete/skip/reschedule actions
- deload/benchmark chips

Remaining targets:

- full calendar layout
- today's planned session execution flow
- edit weekly templates
- block history/detail view

### 6.3 Dashboard / benchmark UI

Status: backend implemented, frontend not confirmed in uploaded component set.

Remaining targets:

- benchmark definition list
- benchmark observation entry
- dashboard KPI display
- readiness flags display
- weak-point management surface

### 6.4 Route model

Status: still tab/surface-based in uploaded docs/source context, not URL-router driven.

Remaining target:

- introduce routing when product sections stabilize: twin, planning, dashboard, history, benchmarks.

## 7. Testing

### Current evidence

The uploaded documentation references tests for dose, state update, ORM persistence, and integration flow. The latest uploaded source did not include the actual test files.

### Priority targets

1. route tests for auth, onboard, simulate, log, next-session
2. planning route tests
3. benchmark observation tests
4. dashboard KPI tests
5. prescriber safety override tests
6. weak-point creation/resolution tests
7. frontend type-check/build smoke tests

## Suggested Priority Order

### Phase 1 — Stabilize implemented backend surface

- [x] auth routes
- [x] onboarding route
- [x] simulate/log routes
- [x] next-session route
- [x] planning routes
- [x] benchmark/dashboard routes
- [x] migrations through a002
- [x] persist all profile fields accepted by onboarding schema
- [x] remove or guard DEV ONLY `user_id` query override in next-session

### Phase 2 — Make planning and benchmark loops product-complete

- [x] full goal default templates
- [ ] proper benchmark session content generation
- [ ] frontend benchmark observation UI
- [ ] frontend dashboard KPI UI
- [ ] weak-point management UI/API

### Phase 3 — Improve prescription quality

- [ ] DB-driven exercise selection
- [ ] deeper block-template use
- [x] deload dosage scaling
- [ ] confidence-weighted weak-point aggregation
- [ ] richer recent-history constraints

### Phase 4 — Testing and production hardening

- [ ] route-level contract tests
- [ ] scenario tests for overload/deload/benchmarks
- [ ] CORS/env hardening
- [ ] structured logging and observability
- [ ] deployment docs and migration checks

### Phase 5 — Advanced model calibration

- [ ] replace simple observation mappings with richer assimilation where justified
- [ ] per-athlete parameter calibration
- [ ] replay tooling from event and observation history
- [ ] model-version migration strategy

## Short Version

The biggest roadmap shift is that planning, benchmarks, dashboard KPIs, and candidate-based prescription are now implemented at MVP level. The next work should focus less on creating the loop and more on tightening correctness, UI completeness, exercise selection quality, and tests.
