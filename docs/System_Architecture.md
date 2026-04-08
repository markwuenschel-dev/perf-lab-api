Performance Lab System Architecture
Purpose

Performance Lab is a training engine that models the athlete rather than a single sport-specific output. The system maintains a latent internal state S(t) representing capacities, batteries, fatigues, and adaptation signals, then uses new workout inputs to update that state and choose the next session.

At a high level, the architecture is:

Workout / Test Input
        ↓
Stress Dose Engine  →  D(t)
        ↓
State Update Engine →  S(t+1)
        ↓
Prescriber          →  u(t)
        ↓
API Response + Persistence

Where:

D(t) is the computed stress dose from a workout or test
S(t) is the current internal athlete state
u(t) is the recommended next session
Design Goals

The system is built around five design goals:

Model the athlete, not just the event
The engine tracks latent internal state across systems rather than only returning a pace, time, or 1RM estimate.
Separate input, state, and prescription
Raw workout logs are not the same thing as internal state.
Internal state is not the same thing as the next prescription.
Support multiple time scales
Fast fatigue and slower adaptation are stored separately.
This lets the system distinguish “can perform today” from “has improved over time.”
Preserve history
State is persisted as a time series, not overwritten in place.
Workout logs are stored independently so the system can be replayed if the dose engine changes.
Stay API-first
The backend exposes simple JSON endpoints while keeping the model complexity internal.
Runtime Components
1. API Layer

The API is served through FastAPI.

There are currently two entry-point patterns in the repo:

app.main:app as the preferred versioned API
main:app as a legacy/demo entry with extra endpoints

The preferred app includes:

auth router
ingest router
prescribe router
/ping health endpoint

Planned routers already signposted in code include:

blocks
weak points
onboarding

This gives the system a clear boundary between transport concerns and training logic.

2. Stress Dose Engine

The dose engine converts a WorkoutLog into a StressDose.

Conceptually, this is a sensor-mapping layer:

WorkoutLog → StressDose

The current StressDose schema includes:

d_met_systemic
d_nm_peripheral
d_nm_central
d_struct_damage
d_struct_signal

This layer exists so the raw workout does not directly mutate athlete state. Instead, the workout is first translated into a normalized internal dose vector.

That separation matters because it lets the same system handle very different modalities while still updating a shared latent state.

3. State Update Engine

The state update engine evolves the athlete from S(t) to S(t+1).

The current orchestration flow in process_new_workout is:

Load the most recent persisted athlete state
Create a baseline state if none exists
Compute D(t) from the workout log
Compute elapsed time dt
Apply the update rules to produce a new state
Persist a new AthleteState row

Important detail: the system persists a new state snapshot rather than mutating the old one.

This is the core modeling decision in the whole project. It preserves temporal history and makes future replay, auditability, and model-version migration much easier.

4. Prescriber

The prescriber reads the latest state and returns the next recommended session.

At the API level this is exposed as a “next session” endpoint, but architecturally it is a controller:

Current State S(t) + Goal + Constraints → Prescription u(t)

The prescriber is intended to incorporate:

current fatigue state
current capacities
goal context
block context
weak-point biasing
exercise availability / equipment constraints

The project’s long-term value lives here. The dose engine and state model explain what is happening; the prescriber decides what to do next.

5. Persistence Layer

The persistence model is intentionally split into different categories of data:

event history for what happened
state history for what the system believed
planning objects for what was intended
supporting metadata for why choices were made

This separation prevents a common failure mode: collapsing logs, state, and plan into one table and losing replayability.

Core Domain Objects
Athlete State S(t)

The unified athlete state stores multiple classes of internal variables.

Capacities

Slow-changing ceilings:

c_met_aerobic
c_nm_force
c_struct

These represent longer-horizon capability rather than same-day readiness.

Batteries

Fast-recharge energetic reserve:

b_met_anaerobic

This captures finite high-intensity work capacity.

Fatigues

Shorter-horizon costs:

f_met_systemic
f_nm_peripheral
f_nm_central
f_struct_damage

These are the main readiness suppressors.

Signals

Adaptation triggers:

s_struct_signal

These represent productive stimuli rather than only cost.

Human Factors

Behavioral / technical modifiers:

habit_strength
skill_state

This is an important design choice: the engine is not purely physiological.

Workout Input

The current workout input schema supports:

timestamp
modality
duration
session RPE
optional RIR
optional distance / volume
sleep quality
inverse life stress

This means the system already treats training as more than external load. It includes human context directly in the state transition.

Control Loop

The active control loop is:

Step 1: Input arrives

A workout log is submitted to the API.

Step 2: Dose is computed

The workout is translated into internal stress terms D(t).

Step 3: Current state is loaded

The engine retrieves the latest stored AthleteState.
If none exists, it creates a baseline S0.

Step 4: Time delta is computed

Elapsed time since the last state is calculated.
If the incoming workout timestamp is older than the current state timestamp, the system clamps dt to zero rather than allowing a negative transition.

Step 5: State is updated

The update engine applies decay, fatigue accumulation, signal generation, and adaptation logic to produce S(t+1).

Step 6: New state is persisted

A new AthleteState row is written.

Step 7: Prescription is produced

The prescriber chooses the next recommended session based on current state and goal.

API Surface
Current implemented API behavior
POST /v1/simulate-dose

Pure transform:

input: WorkoutLog
output: StressDose

No state mutation.

POST /v1/log-workout

State-changing path:

input: WorkoutLog
behavior: computes dose, updates state, persists result
output: updated UnifiedStateVector
GET /v1/next-session

Control output:

input: latest state + goal
output: WorkoutPrescription
GET /ping

Health check.

Frontend Architecture

The current frontend acts as a thin control console over the API.

It supports three main behaviors:

simulate a dose without changing state
log a workout and update S(t)
request a new prescription

The UI therefore mirrors the actual backend architecture rather than inventing a separate front-end-only mental model. That is the right design.

Planning Layer

Beyond the immediate state loop, the architecture includes a macro-planning layer:

MesocycleBlock
PlannedSession

This allows the system to bridge from:

long-range goal structure
to
daily adaptive prescription

A block defines intent, template, and cadence.
A planned session defines a concrete training slot.
The prescriber can then populate prescribed content lazily when the athlete opens that day’s session.

This is the right boundary between planning and adaptation:

the block sets direction
the current state sets today’s constraints
Weak Point Layer

Weak points are modeled explicitly as first-class data, not just hidden heuristics.

Each weak point includes:

canonical tag
source
confidence
optional note
resolution status

This design is strong because it lets the system distinguish:

what the user thinks is weak
what a benchmark shows is weak
what the model infers is weak

Those signals can later be aggregated by the prescriber instead of being flattened into a binary flag.

Exercise Library Layer

The exercise library is the catalog the prescriber uses to turn an abstract session into concrete movements.

Each exercise stores:

modality
movement pattern
primary / secondary muscles
equipment requirements
load type
skill demand
impact level
weak-point tags
benchmark flag
coaching notes
extra metadata

This is an important architectural decision: prescription is not just “choose intensity.” It is also “choose the right implementation given constraints.”

Persistence Strategy

The project uses an event-plus-state model:

WorkoutLog = what happened
AthleteState = what the engine believed after processing it
PlannedSession = what was intended
WeakPoint = what the system thinks needs biasing
Exercise = what can be prescribed

That separation should be preserved.

A common bad refactor would be trying to merge logs and state to “simplify” the schema. That would make replay, debugging, and model evolution much worse.

Invariants

These are the architectural invariants the code should continue to respect.

1. State snapshots are append-only

Never overwrite athlete state in place.

2. Workout logs remain separate from state

Logs are raw events; state is model interpretation.

3. The dose engine is an intermediate layer

Workouts should not directly modify state without going through D(t).

4. Prescription depends on current state, not just the last workout

The controller should act on modeled readiness and trend, not only recency.

5. Planning and adaptation stay distinct

Blocks define structure; state defines day-level modulation.

Current Limitations

Several parts of the architecture are clearly planned but not fully closed yet:

Alembic migrations are not yet set up
modality-aware versioning is still planned
persistent onboarding flow is signposted but not fully wired
blocks / weak-point / onboarding routers are noted but not active
data assimilation / EKF is conceptual rather than implemented
some frontend sections are explicitly demo placeholders

These are normal at this stage, but they should be documented as planned layers rather than implied to be complete.