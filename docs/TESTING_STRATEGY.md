# Performance Lab Testing Strategy

## Purpose

This document explains what should be tested in Performance Lab, why those tests matter, and where exact assertions are appropriate versus where directional or scenario assertions are better.

Performance Lab is not a typical CRUD API. It is a stateful control system with:

- internal model transitions
- approximate dose and physiology mappings
- adaptive prescription decisions
- planning state
- benchmark-driven assimilation
- persistence of event, observation, state, and KPI history

The central testing question is not only:

> Does the endpoint return 200?

It is:

> Did the engine behave correctly, safely, and consistently under realistic training conditions?

## Running the suite

The backend tests run against a **real PostgreSQL** — there is no in-memory fake. Any test
that requests the `async_db` or `http_client` fixture is auto-marked `requires_db`.

```bash
uv run pytest -q                     # full suite (serial)
uv run pytest -q -n auto             # parallel — how CI runs it
uv run pytest -q -m "not requires_db"  # unit-only; no database needed
```

Point the suite at a database with `TEST_DATABASE_URL` (falls back to
`postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test`, the
`docker-compose` dev DB). The schema is migrated to head once per session and each test
truncates the data, so tests start empty.

**Parallel runs shard the database, not just the process.** Per-test isolation is a global
`TRUNCATE`, so under `-n auto` one worker would wipe another's rows mid-test. Each xdist
worker therefore gets its **own** database — `perflab_test_gw0`, `perflab_test_gw1`, … —
auto-created on first use. Serial runs keep the plain database name, so nothing changes
without `-n auto`. (This is what closed the old cross-worker race; see `tests/conftest.py`.)

**A missing database is a hard failure in CI, never a silent skip.** CI sets `REQUIRE_DB=1`,
which turns an unreachable database into a real error so the integration suite cannot pass by
skipping (INT-23). Locally, without `REQUIRE_DB`, `requires_db` tests skip when Postgres is
down — set `REQUIRE_DB=1` to force the same hard-fail behavior as CI.

## Current Scope to Test

Backend surfaces currently include:

- auth routes
- onboarding
- simulate-dose
- log-workout
- next-session
- planning blocks/sessions/today
- benchmark definitions/observations/recompute
- dashboard KPIs/domain-summary/readiness
- state update engine
- dose engine
- prescriber candidate scoring/finalization
- migrations through `a002`

Frontend surfaces currently include:

- auth strip
- sessionStorage auth state
- onboarding gate
- digital twin loop
- planning panel
- typed API client
- manually mirrored DTOs
- Tailwind/design system config

## Test Pyramid

### 1. Unit tests

Fast tests for:

- dose calculation
- state update rules
- state bridge conversions
- planning template generation helpers
- benchmark mapping helpers
- derived metric formula helpers
- prescriber scoring and safety helpers
- frontend pure helpers

### 2. Service integration tests

Tests across:

- DB session + services
- state initialization + workout processing
- block creation + session generation
- benchmark observation + state/KPI/weak-point updates
- dashboard payload composition

### 3. Route contract tests

Tests for:

- auth behavior
- request validation
- response shape
- error detail shape
- auth-required route protection
- non-mutating vs mutating semantics

### 4. Scenario tests

Longer flows:

- first-run lifecycle
- repeated hard sessions
- deload week behavior
- planned vs completed session linkage
- benchmark deficit -> weak point -> prescription bias
- benchmark improvement -> weak point resolution
- KPI-driven prescription shift

### 5. Frontend tests / checks

Minimum required checks:

- `npx tsc --noEmit`
- `npm run build`
- API client contract smoke tests where possible
- component rendering tests for core surfaces if test framework is added

## Hard Invariants

These should use strict assertions.

### 1. Append-only state history

Logging a workout must create a new `AthleteState` row. It must not overwrite the previous row.

Assert:

- state row count increases
- previous row values remain unchanged
- latest-state query returns the newest timestamped row

### 2. Workout logs remain separate from state

`POST /v1/log-workout` must create a `WorkoutLog` row and an `AthleteState` row. They are different objects.

Assert:

- workout event exists
- dose snapshot is stored
- state row exists separately

### 3. Simulate-dose does not mutate state

`POST /v1/simulate-dose` is pure.

Assert:

- no workout rows created
- no athlete state rows created or modified

### 4. Negative time deltas are clamped

If a workout timestamp is older than the current state timestamp, effective `dt` must be zero rather than negative.

Assert:

- no crash
- new state is valid
- no negative time transition propagates

### 5. Fatigue and tissue bounds

Fatigue and tissue vectors must stay within [0, 100].

Relevant fields:

- `fatigue_f.*`
- `tissue_t.*`
- legacy fatigue mirrors

### 6. Legacy mirrors stay aligned

State bridge conversion should keep legacy fields in sync with decomposed vectors.

Assert examples:

- `c_nm_force == capacity_x.max_strength * 10.0`
- `b_met_anaerobic == capacity_x.glycolytic * 300.0`
- legacy fatigue values reflect `fatigue_f` as defined by `sync_legacy_from_vectors()`

### 7. Planned session linkage

When a workout provides `planned_session_id`, the planned session should be marked complete and linked.

When no ID is provided, same-day pending match should work best-effort.

Assert:

- planned session status becomes completed
- `workout_log_id` is set
- workout row has `planned_session_id`
- `completed_at` is set

### 8. Benchmark observations preserve timestamp semantics

Benchmark-driven state rows should use the observation's `observed_at` timestamp when provided.

Assert:

- new state timestamp equals observation time
- state history remains queryable in chronological order

### 9. Weak-point benchmark feedback

Valid normalized benchmark observations should create or resolve weak points based on thresholds.

Assert:

- normalized `< 40` creates/refreshes benchmark-sourced weak points
- normalized `> 65` resolves active matching benchmark weak points
- invalid observations do not update weak points

### 10. Derived KPI recomputation writes snapshots

Recomputing derived metrics should write `DerivedMetricSnapshot` rows for computable definitions.

Assert:

- snapshots are created
- contributing observations are recorded when applicable
- missing inputs do not crash computation

### 11. Auth route behavior

Assert:

- duplicate email returns 409
- too-short password fails validation
- bad login returns 401
- inactive user returns 403
- authenticated `/auth/me` returns user

### 12. Alembic migration integrity

Assert against a fresh database:

- `alembic upgrade head` succeeds
- foundational tables exist
- benchmark/KPI tables exist
- planned-session benchmark columns exist

## Approximate / Directional Assertions

These should not overfit exact numbers unless deliberately snapshotting a reference implementation.

### Dose calculations

Prefer assertions like:

- higher RPE increases dose
- poor sleep/life stress increases fatigue load
- running creates more metabolic/impact stress than equivalent strength session
- strength/hypertrophy sessions create relevant neuromuscular/structural signal
- per-exercise phi vectors affect dose relative to fallback defaults

Avoid brittle assertions such as exact floating-point dose values.

### State updates

Prefer assertions like:

- fatigue decays over rest
- hard sessions increase fatigue
- high fatigue suppresses adaptation efficiency
- valid adaptation contribution nudges capacity upward slowly

Avoid exact long-run physiology numbers unless freezing a model version.

### Prescriber selection

Prefer assertions like:

- high structural/tendon stress returns recovery or tissue-deload class
- high CNS fatigue avoids max neural work
- block category match receives priority when safe
- weak-point tags appear in explanation
- equipment constraints produce compatible or fallback exercises

Avoid locking to one exact string if multiple safe recommendations are valid.

## Route Test Matrix

### Auth

- register success
- register duplicate
- password validation
- login success
- login failure
- me with token
- me without token

### Onboarding

- onboard fills profile shell
- self-reported weak points created
- baseline state created
- squat 1RM affects force capacity
- onboarding requires auth

### Ingest

- simulate-dose response shape
- simulate-dose non-mutating
- log-workout response shape
- log-workout creates workout log
- log-workout appends state
- log-workout links planned session by ID
- log-workout same-day matches planned session
- log-workout stores dose snapshot
- backdated log clamps `dt`

### Prescribe

- next-session requires auth
- no state auto-initializes baseline
- response includes `model_version`, exercises, and `why`
- active weak points appear in constraints
- active planned session writes `prescribed_content`
- high fatigue/tissue state triggers recovery class
- DEV ONLY user override should be removed or explicitly test-guarded before production

### Planning

- create block creates sessions
- default templates work
- deload flags generated
- benchmark flags generated
- list blocks scoped to user
- list sessions date-window filtering
- patch status
- patch scheduled date
- today endpoint no session
- today endpoint session without state
- today endpoint session with prescription

### Benchmarks

- list definitions
- create observation unknown code -> 400
- derived-only definition cannot receive observation
- valid observation creates row
- valid observation applies mappings to state
- valid observation triggers weak-point feedback
- valid observation triggers derived KPI recompute best-effort
- list observations by code
- limit bounds enforced

### Dashboard

- KPI bundle includes latest snapshot per metric
- primary anchors dedupe to latest per code
- domain summary filters by domain
- readiness returns no-state note when no state
- readiness flags reflect latest KPI values

## Scenario Tests

### 1. First-run lifecycle

```text
register -> login -> onboard -> next-session -> log-workout -> next-session
```

Expected:

- profile exists
- baseline state exists
- first prescription returns valid shape
- workout log persists
- state history length increases
- second prescription reflects updated state

### 2. Planning lifecycle

```text
onboard -> create block -> list sessions -> get today -> log same-day workout -> session completed
```

Expected:

- block created
- sessions generated
- today's session returned when scheduled
- prescription content written
- log links session
- completed status and `workout_log_id` set

### 3. Overload behavior

Simulate repeated high-RPE sessions close together.

Expected:

- fatigue accumulates
- tissue stress accumulates
- next prescription shifts lower cost or recovery-oriented when thresholds are crossed

### 4. Deload behavior

Create a block with deload cadence and inspect week N.

Expected:

- planned sessions marked deload
- prescription explanation includes deload constraint
- eventual dosage should be lower than normal block week once implemented fully

### 5. Benchmark deficit and improvement

Post low normalized benchmark, then high normalized benchmark.

Expected:

- deficit creates benchmark weak point
- prescription includes weak-point constraint
- improvement resolves weak point
- future prescription no longer includes resolved tag

### 6. KPI-driven prescription

Create observations that compute a KPI used by the prescriber.

Expected:

- KPI snapshot exists
- `latest_kpi_values()` returns code
- prescription branch/rationale changes when KPI crosses heuristic threshold

## Frontend Checks

### Required before docs/API sync commit

```bash
npx tsc --noEmit
npm run build
npm run lint
```

### API type sync checks

When backend schemas change, inspect:

- `src/types.ts`
- `src/trainingGoals.ts`
- `src/api/perfLabClient.ts`
- `DigitalTwinPanel.tsx`
- `OnboardingForm.tsx`
- `PlanningPanel.tsx`

### Useful future component tests

- unauthenticated state shows sign-in prompts
- registration sets onboarding pending
- onboarding submit calls `completeOnboarding`
- planning panel creates block and reloads sessions
- session actions call patch endpoint
- digital twin log flow refreshes next session after logging
- 401 calls unauthorized bridge and clears session

## Current Test Coverage Note

Previous project docs referenced unit and integration tests for dose engine, state update, ORM persistence, and integration flow. Those test files were not included in the latest uploaded source set, so this document describes the target strategy and the behaviors that should be verified rather than claiming current test counts.

## Short Version

The highest-value tests are:

1. append-only state history
2. simulate vs log mutation separation
3. planned-session linkage
4. benchmark observation -> state/KPI/weak-point updates
5. prescriber safety overrides
6. route response contracts
7. frontend type sync/build checks
