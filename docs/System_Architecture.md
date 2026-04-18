# Performance Lab System Architecture

## Purpose

Performance Lab is a training engine that models the athlete rather than a single sport-specific output. The system maintains a latent internal state `S(t)` representing capacities, batteries, fatigues, and adaptation signals, then uses new workout inputs to update that state and choose the next session.

At a high level, the architecture is:

```
Workout / Test Input
        ↓
Stress Dose Engine  →  D(t)
        ↓
State Update Engine →  S(t+1)
        ↓
Prescriber          →  u(t)
        ↓
API Response + Persistence
```

Where:

- `D(t)` is the computed stress dose from a workout or test
- `S(t)` is the current internal athlete state
- `u(t)` is the recommended next session

---

## Design Goals

The system is built around five design goals:

### 1. Model the athlete, not just the event
The engine tracks latent internal state across systems rather than only returning a pace, time, or 1RM estimate.

### 2. Separate input, state, and prescription
- Raw workout logs are not the same thing as internal state.
- Internal state is not the same thing as the next prescription.

### 3. Support multiple time scales
Fast fatigue and slower adaptation are stored separately. This lets the system distinguish "can perform today" from "has improved over time."

### 4. Preserve history
State is persisted as a time series, not overwritten in place. Workout logs are stored independently so the system can be replayed if the dose engine changes.

### 5. Stay API-first
The backend exposes simple JSON endpoints while keeping the model complexity internal.

---

## Runtime Components

### 1. API Layer

The API is served through FastAPI.

There are currently two entry-point patterns in the repo:

- `app.main:app` — preferred versioned API
- `main:app` — legacy/demo entry with extra endpoints

The preferred app includes:

- `auth` router
- `ingest` router
- `prescribe` router
- `/ping` health endpoint

Planned routers already signposted in code:

- `blocks`
- `weak_points`
- `onboarding`

This gives the system a clear boundary between transport concerns and training logic.

---

### 2. Stress Dose Engine

The dose engine converts a `WorkoutLog` into a `StressDose`.

```
WorkoutLog → StressDose
```

The current `StressDose` schema includes:

| Field | Description |
|---|---|
| `d_met_systemic` | Metabolic systemic stress |
| `d_nm_peripheral` | Neuromuscular peripheral stress |
| `d_nm_central` | Neuromuscular central stress |
| `d_struct_damage` | Structural damage |
| `d_struct_signal` | Structural adaptation signal |

This layer exists so the raw workout does not directly mutate athlete state. Instead, the workout is first translated into a normalized internal dose vector. That separation lets the same system handle very different modalities while still updating a shared latent state.

---

### 3. State Update Engine

The state update engine evolves the athlete from `S(t)` to `S(t+1)`.

The current orchestration flow in `process_new_workout` is:

1. Load the most recent persisted athlete state
2. Create a baseline state if none exists
3. Compute `D(t)` from the workout log
4. Compute elapsed time `dt`
5. Apply the update rules to produce a new state
6. Persist a new `AthleteState` row

> **Important:** The system persists a new state snapshot rather than mutating the old one. This is the core modeling decision in the whole project. It preserves temporal history and makes future replay, auditability, and model-version migration much easier.

---

### 4. Prescriber

The prescriber reads the latest state and returns the next recommended session.

```
Current State S(t) + Goal + Constraints + Weak Points + Block Context → Prescription u(t)
```

The prescriber incorporates:

- Current fatigue state and capacities
- Goal context
- Active MesocycleBlock + today's PlannedSession (block_context — applies a
  +0.15 score bias toward candidates matching the planned session category)
- Active unresolved WeakPoint tags (active_weak_points — annotates
  constraints_applied in the explanation with weak_point:{tag} entries)
- Exercise availability / equipment constraints (available_equipment parameter
  exists; equipment query from AthleteProfile is planned)

The resulting `WorkoutPrescription` includes `model_version`, `exercises`, and
a `PrescriptionExplanation` (why) with structured rationale.

If a `PlannedSession` exists for today, the prescriber writes the generated
prescription back to `PlannedSession.prescribed_content`.

The project's long-term value lives here. The dose engine and state model explain what is happening; the prescriber decides what to do next.

---

### 5. Persistence Layer

The persistence model is intentionally split into different categories of data:

| Category | Purpose |
|---|---|
| Event history | What happened |
| State history | What the system believed |
| Planning objects | What was intended |
| Supporting metadata | Why choices were made |

This separation prevents a common failure mode: collapsing logs, state, and plan into one table and losing replayability.

---

## Core Domain Objects

### Athlete State `S(t)`

The unified athlete state stores multiple classes of internal variables.

#### Capacities
Slow-changing ceilings:
- `c_met_aerobic`
- `c_nm_force`
- `c_struct`

These represent longer-horizon capability rather than same-day readiness.

#### Batteries
Fast-recharge energetic reserve:
- `b_met_anaerobic`

This captures finite high-intensity work capacity.

#### Fatigues
Shorter-horizon costs:
- `f_met_systemic`
- `f_nm_peripheral`
- `f_nm_central`
- `f_struct_damage`

These are the main readiness suppressors.

#### Signals
Adaptation triggers:
- `s_struct_signal`

These represent productive stimuli rather than only cost.

#### Human Factors
Behavioral / technical modifiers:
- `habit_strength`
- `skill_state`

This is an important design choice: the engine is not purely physiological.

---

### Workout Input

The current workout input schema supports:

- `timestamp`
- `modality`
- `duration`
- Session RPE
- Optional RIR
- Optional distance / volume
- Sleep quality
- Inverse life stress

This means the system already treats training as more than external load — it includes human context directly in the state transition.

---

## Control Loop

### Step 1: Input arrives
A workout log is submitted to the API.

### Step 2: Dose is computed
The workout is translated into internal stress terms `D(t)`.

### Step 3: Current state is loaded
The engine retrieves the latest stored `AthleteState`. If none exists, it creates a baseline `S0`.

### Step 4: Time delta is computed
Elapsed time since the last state is calculated. If the incoming workout timestamp is older than the current state timestamp, the system clamps `dt` to zero rather than allowing a negative transition.

### Step 5: State is updated
The update engine applies decay, fatigue accumulation, signal generation, and adaptation logic to produce `S(t+1)`.

### Step 6: New state is persisted
A new `AthleteState` row is written.

### Step 7: Prescription is produced
The prescriber chooses the next recommended session based on current state and goal.

---

## API Surface

### Implemented endpoints (as of v0.3, April 2026)

#### `POST /v1/onboard`
First-run setup. Creates AthleteProfile + optional WeakPoints + seeds S0.
- **Input:** `OnboardRequest` (email, experience_level, squat_1rm_kg, …)
- **Output:** `OnboardResponse` (user_id, profile_id, message)
- **Mutates state:** Yes — seeds baseline AthleteState

#### `POST /v1/simulate-dose`
Pure transform — no state mutation.
- **Input:** `WorkoutLog`
- **Output:** `StressDose`

#### `POST /v1/log-workout`
State-changing path.
- **Input:** `WorkoutLog`
- **Behavior:** Resolves exercise phi vectors, computes dose, updates state, persists new row atomically
- **Output:** Updated `UnifiedStateVector` (includes `model_version`)

#### `GET /v1/next-session`
Control output.
- **Input:** Latest state + goal (query param)
- **Behavior:** Queries active WeakPoints + active MesocycleBlock/PlannedSession;
  may write prescription to PlannedSession.prescribed_content
- **Output:** `WorkoutPrescription` (includes `model_version`, `exercises`, `why`)

#### `GET /ping`
Health check.

---

## Frontend Architecture

The current frontend acts as a thin control console over the API.

It supports three main behaviors:

1. Simulate a dose without changing state
2. Log a workout and update `S(t)`
3. Request a new prescription

The UI mirrors the actual backend architecture rather than inventing a separate front-end-only mental model.

---

## Planning Layer

Beyond the immediate state loop, the architecture includes a macro-planning layer:

- `MesocycleBlock`
- `PlannedSession`

This allows the system to bridge from long-range goal structure to daily adaptive prescription.

- A **block** defines intent, template, and cadence.
- A **planned session** defines a concrete training slot.
- The **prescriber** can then populate prescribed content lazily when the athlete opens that day's session.

This is the right boundary between planning and adaptation:
- The block sets direction.
- The current state sets today's constraints.

---

## Weak Point Layer

Weak points are modeled explicitly as first-class data, not just hidden heuristics.

Each weak point includes:

- Canonical tag
- Source
- Confidence
- Optional note
- Resolution status

This design lets the system distinguish:

- What the **user thinks** is weak
- What a **benchmark shows** is weak
- What the **model infers** is weak

Those signals can later be aggregated by the prescriber instead of being flattened into a binary flag.

---

## Exercise Library Layer

The exercise library is the catalog the prescriber uses to turn an abstract session into concrete movements.

Each exercise stores:

- Modality
- Movement pattern
- Primary / secondary muscles
- Equipment requirements
- Load type
- Skill demand
- Impact level
- Weak-point tags
- Benchmark flag
- Coaching notes
- Extra metadata

Prescription is not just "choose intensity" — it is also "choose the right implementation given constraints."

---

## Persistence Strategy

The project uses an event-plus-state model:

| Object | Represents |
|---|---|
| `WorkoutLog` | What happened |
| `AthleteState` | What the engine believed after processing it |
| `PlannedSession` | What was intended |
| `WeakPoint` | What the system thinks needs biasing |
| `Exercise` | What can be prescribed |

That separation should be preserved. A common bad refactor would be merging logs and state to "simplify" the schema — that would make replay, debugging, and model evolution much worse.

---

## Invariants

These are the architectural invariants the code should continue to respect.

1. **State snapshots are append-only** — never overwrite athlete state in place.
2. **Workout logs remain separate from state** — logs are raw events; state is model interpretation.
3. **The dose engine is an intermediate layer** — workouts should not directly modify state without going through `D(t)`.
4. **Prescription depends on current state, not just the last workout** — the controller should act on modeled readiness and trend, not only recency.
5. **Planning and adaptation stay distinct** — blocks define structure; state defines day-level modulation.

---

## Current Limitations

The core loop is operational. Remaining planned-but-not-yet-implemented layers:

- Block creation and calendar-generation routes (`MesocycleBlock` CRUD) — the
  prescriber can read blocks if rows exist, but there is no public API to create
  or manage them yet
- `weak_points` write routes — weak points can be seeded via `/v1/onboard` but
  there is no standalone API to add, update, or resolve them yet
- Equipment-aware exercise selection — `AthleteProfile.equipment` is stored but
  not yet queried in the prescriber's exercise filter step
- Modality-aware versioning — planned but not implemented
- Data assimilation / EKF correction — conceptual, not implemented
- Some frontend sections remain demo placeholders pending block/history views

Items completed in v0.3:

- Alembic migrations: a000 (foundational) + a001 (benchmark KPI tables)
- `POST /v1/onboard` endpoint (profile + weak points + baseline state)
- Profile-aware baseline seeding (4-tier capacity table)
- Weak-point injection into prescriber (DB query + constraints_applied annotation)
- Block context injection into prescriber (score bias + prescription persistence)
- `model_version` on `UnifiedStateVector` and `WorkoutPrescription`
- Import chain corrected: service layer imports from v0.3 engine modules
- 20 unit + integration tests
