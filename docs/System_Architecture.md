# Performance Lab System Architecture

## Purpose

Performance Lab is an adaptive training engine that models the athlete rather than a single workout, race, or lift. It maintains a latent internal state `S(t)` across capacities, fatigues, tissue stress, skill, habit, and adaptation signals. It uses workouts and benchmark observations to update that state, then prescribes the next useful session.

High-level architecture:

```text
Workout Log
   -> Stress Dose Engine D(t)
   -> State Update Engine S(t+1)
   -> Prescriber u(t)
   -> API Response + Persistence

Benchmark Observation
   -> Observation Mapping
   -> State Nudge S(t+1)
   -> Weak-Point / KPI Updates
   -> Prescriber Context
```

## Design Goals

1. Model the athlete, not just the event.
2. Separate raw inputs, modeled state, plans, measurements, and prescriptions.
3. Preserve history.
4. Support multiple time scales.
5. Stay API-first.
6. Keep the system legible enough to debug.

## Runtime Entrypoint

The canonical backend entrypoint is:

```bash
uvicorn app.main:app --reload
```

The FastAPI application is `app.main:app`.

Application startup:

- verifies database connectivity
- checks Alembic head
- does not mutate schema
- does not call `Base.metadata.create_all`

Schema management is handled through Alembic migrations only.

## Router Layout

Auth and legacy routes are outside `/v1`:

```text
/auth/register
/auth/token
/auth/me
/compute-metrics
/program/run
/program/strength
/ping
```

Modern routes are under `/v1`:

```text
/v1/onboard
/v1/simulate-dose
/v1/log-workout
/v1/next-session
/v1/planning/*
/v1/benchmarks/*
/v1/dashboard/*
```

Routers included by the app:

- auth
- legacy
- ingest
- prescribe
- benchmarks
- dashboard
- planning
- onboard router

## Core Backend Components

### 1. API Layer

FastAPI routes handle transport, auth dependencies, request/response models, and error shaping. They should stay thin.

Examples:

- `ingest.py` delegates workout processing to `state_service`
- `prescribe.py` gathers context and delegates prescription to `recommend_next_session`
- `planning.py` delegates block/session generation to `planning_service`
- `benchmarks.py` delegates observation handling to `benchmark_service`

### 2. Stress Dose Engine

Preferred implementation:

```python
app.logic.dose_engine_v0.calculate_stress_dose
```

Deprecated compatibility module:

```python
app.logic.dose_engine
```

The dose engine converts a `WorkoutLog` into a `StressDose`.

It supports:

- modality-level fallback
- dominant movement pattern inference
- per-exercise dose when entries are supplied
- phi adaptation/fatigue/tissue vectors
- energy mix
- six-axis dose vector
- adaptation contribution vector
- legacy scalar dose channels

### 3. State Update Engine

Preferred implementation:

```python
app.logic.state_update_v0.update_athlete_state
```

The state update engine applies:

- multi-timescale fatigue decay
- tissue stress decay
- recovery effects from sleep and life stress
- fatigue impulses from dose
- tissue impulses from dose
- structural signal accumulation
- explicit adaptation gains by capacity axis
- cross-talk effects
- legacy scalar mirror syncing

### 4. State Service

Primary service:

```python
app.services.state_service.process_new_workout
```

Responsibilities:

1. fetch latest `AthleteState`
2. build baseline state if missing
3. resolve exercise phi vectors from the `Exercise` table
4. calculate stress dose
5. persist `WorkoutLog` and dose snapshot
6. link or same-day match a planned session
7. mark planned session completed
8. clamp negative `dt`
9. persist new `AthleteState`
10. return unified state

Baseline initialization is handled by:

```python
initialize_athlete_state(...)
```

### 5. State Bridge

`app.engine.state_bridge` maps between ORM rows and `UnifiedStateVector`.

It keeps these aligned:

- legacy scalar columns
- `engine_state` JSONB
- Pydantic `UnifiedStateVector`

This lets old clients continue using scalar fields while the engine evolves through decomposed vectors.

### 6. Planning Service

`create_block_with_sessions()` creates a `MesocycleBlock` and generated `PlannedSession` rows.

Current behavior:

- computes `end_date`
- uses default goal templates when no weekly template is supplied
- marks deload weeks
- marks periodic benchmark sessions
- commits generated rows

`get_today_session()` returns today's pending planned session.

### 7. Benchmark Service

Benchmark service supports:

- listing benchmark definitions
- listing observations
- creating observations
- applying observation mappings to state
- creating/resolving benchmark weak points
- recomputing derived KPI snapshots

Benchmark observations can create new `AthleteState` rows independently of workout logs.

### 8. Dashboard Service

Dashboard service supports:

- latest valid observation lookup by benchmark code
- latest KPI values
- derived metric recomputation
- dashboard KPI bundle
- domain summary
- readiness payload with KPI flags

### 9. Prescriber

`recommend_next_session()` is candidate-based.

Inputs:

- state
- goal
- recent sessions
- KPI summary
- active weak points
- available equipment
- block context

Pipeline:

```text
safety overrides
  -> goal candidates + readiness redirects
  -> scoring
  -> finalization
  -> equipment exercise payload
  -> explanation annotations
```

Finalization attaches validation, provenance, source alignment, warnings, and structured template metadata.

## Persistence Architecture

The schema separates data by meaning.

| Layer | Tables | Meaning |
|---|---|---|
| Identity | users, athlete_profiles | account + setup |
| Event history | workout_logs | completed workouts |
| Measurement history | benchmark_observations | tests / benchmarks |
| State history | athlete_states | model belief timeline |
| Planning | mesocycle_blocks, planned_sessions | intended future work |
| Bias signals | weak_points | limitations and evidence |
| Movement library | exercises | prescribable movements |
| Metrics | benchmark_definitions, derived_metric_definitions, derived_metric_snapshots, observation_mappings | benchmark/KPI system |

## Core Domain Objects

### UnifiedStateVector

Contains:

- `capacity_x`
- `fatigue_f`
- `tissue_t`
- legacy scalar mirrors
- `s_struct_signal`
- `habit_strength`
- `skill_state`
- `model_version`

### WorkoutLog DTO

Contains:

- session timestamp
- modality
- duration
- RPE/RIR
- distance/volume
- movement pattern
- novelty
- estimated sets
- sleep/stress
- optional exercise entries
- planning/benchmark metadata

### StressDose

Contains:

- six-dimensional dose
- adaptation contribution
- legacy scalar channels

### WorkoutPrescription

Contains:

- session display fields
- engine model version
- exercises
- structured `why`

### BenchmarkObservation

Contains:

- benchmark definition reference
- raw and normalized values
- observation metadata
- validity/source fields

## Control Loops

### Workout loop

```text
User logs workout
  -> service resolves exercise phi vectors
  -> dose engine computes D(t)
  -> workout log + dose snapshot persisted
  -> state update computes S(t+1)
  -> new athlete state row persisted
  -> planned session completed if matched
```

### Prescription loop

```text
Client asks next-session
  -> latest state loaded or initialized
  -> weak points loaded
  -> active block / today session loaded
  -> recent workouts loaded
  -> latest KPIs loaded
  -> prescriber recommends u(t)
  -> planned session content written if applicable
```

### Planning loop

```text
Client creates block
  -> block persisted
  -> sessions generated from template
  -> deload and benchmark flags assigned
  -> today/session endpoints expose slots
```

### Benchmark loop

```text
Client posts observation
  -> definition loaded
  -> observation persisted
  -> observation mappings applied to state if valid
  -> weak points updated
  -> derived KPIs recomputed
```

## Frontend Architecture Summary

The frontend is a React/TypeScript SPA that mirrors backend domain concepts.

Main surfaces:

- auth strip
- onboarding form
- digital twin panel
- planning panel
- legacy field-test column

The central API wrapper is `src/api/perfLabClient.ts`.

The central type mirror is `src/types.ts`.

Frontend control loop:

```text
GET /v1/next-session
POST /v1/simulate-dose
POST /v1/log-workout
GET /v1/next-session
```

Planning UI adds:

```text
POST /v1/planning/blocks
GET /v1/planning/blocks
GET /v1/planning/sessions
PATCH /v1/planning/sessions/{id}
```

## Invariants

1. State snapshots are append-only.
2. Workout logs remain separate from state.
3. Benchmarks remain separate from workouts unless explicitly linked by flow.
4. Plans remain separate from completed logs.
5. Dose is an intermediate layer for workout-driven updates.
6. Observation mappings are the benchmark-to-state bridge.
7. Prescription depends on current state and context, not only the last workout.
8. Weak points bias but do not dominate.
9. Equipment constraints should eventually be hard filters.
10. Alembic controls schema state.

## Current Limitations

Implemented:

- core workout loop
- onboarding
- planning MVP
- benchmark/KPI MVP
- candidate prescriber
- validation/explainability finalization
- frontend planning and twin surfaces

Still missing or partial:

- standalone weak-point management API
- exercise library management API
- DB-driven exercise selection in prescriber
- full frontend benchmark/dashboard UI
- route and scenario tests in uploaded source set
- removal of DEV ONLY `user_id` next-session override
- full persistence of all onboarding baseline fields
