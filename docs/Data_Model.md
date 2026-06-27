# Performance Lab Data Model

## Purpose

This document explains how Performance Lab stores identity, profile setup, workout history, internal state, planning data, weak-point signals, benchmark observations, derived KPIs, exercise metadata, and prescription support data.

The core rule is:

> Do not confuse what happened, what the system believes, what was planned, and what was measured.

That rule drives the schema.

## Data Model Principles

### 1. Event data and state data are different

A workout log records what happened. An athlete state row records what the model believes after processing events or observations.

### 2. State is historical, not mutable-in-place

The system stores a time series of `AthleteState` rows. The latest row is the current state, but prior rows are retained.

### 3. Plans are not logs

A planned session is an intended future slot. A workout log is an observed completed event. The correct relationship is a fulfillment link, not overwriting the plan with observed data.

### 4. Weak points are probabilistic signals

Weakness is stored as tagged evidence with source and confidence, not as one permanent athlete trait.

### 5. Exercise selection is metadata-driven

Exercise rows carry movement, equipment, tissue, fatigue, adaptation, and weak-point tags so the prescriber can move from abstract intent to concrete exercise choices.

### 6. Benchmarks are observations, not workouts by default

Benchmarks can be linked to planned session flow or workout logs, but benchmark observations have their own schema and can nudge state through observation mappings.

### 7. Derived KPIs are snapshots

Derived metrics are computed from observations and profile context, then stored as time-stamped snapshots.

### 8. Baseline anchors, not a single baseline measurement

A single capacity test is insufficient for most training goals.  Performance Lab distinguishes four anchor types for every goal:

- **Capacity anchor** — what can the athlete currently do? (e.g. 1RM, 5K time, vertical jump)
- **Load-tolerance anchor** — how much stress can they currently recover from? (e.g. weekly hard sets, weekly mileage, explosive rep capacity)
- **Risk / tissue anchor** — what limits safe progression? (e.g. tendon irritation, injury history, technical breakdown threshold)
- **Retest metric** — what should be measured periodically to recalibrate the model?

These are represented as `GoalLoadDefinition` records in `app/logic/goal_load_definitions.py` (backend) and `web/src/perflab/goalLoadDefinitions.ts` (frontend). Each of the 14 supported training goals has exactly one definition.

Goal-specific notes:
- **Running / endurance goals**: distinguish performance capacity from durability capacity. A 5K time captures speed, not the ability to absorb chronic mileage.
- **Strength / powerlifting**: a 1RM anchors force capacity, but does not reveal how much weekly hard-set load the athlete can recover from.
- **Gymnastics / calisthenics / grip**: connective-tissue tolerance must be tracked separately from muscle capacity, as it adapts more slowly and fails silently.
- **MetCon**: a single benchmark score hides the limiting system. Decompose by metabolic density, movement interference, local muscular endurance, skill bottleneck, and eccentric damage.

The `GoalLoadDefinition` model is explanatory and measurement-first. It is shaped to later feed benchmark selection, onboarding question routing, prescriber rationale, validator constraints, and state-update calibration.

## Entity Overview

```text
User
 └─ AthleteProfile (1:1)

User
 ├─ AthleteState (1:N)
 ├─ WorkoutLog (1:N)
 ├─ MesocycleBlock (1:N)
 ├─ PlannedSession (1:N through blocks)
 ├─ WeakPoint (1:N)
 ├─ BenchmarkObservation (1:N)
 └─ DerivedMetricSnapshot (1:N)

MesocycleBlock
 └─ PlannedSession (1:N)

PlannedSession
 └─ WorkoutLog (0:1 fulfillment link)

BenchmarkDefinition
 ├─ BenchmarkObservation (1:N)
 └─ ObservationMapping (1:N)

DerivedMetricDefinition
 └─ DerivedMetricSnapshot (1:N)

Exercise
 └─ referenced by dose/prescription logic, not owned by user
```

## Entity Categories

Identity and setup:

- `User`
- `AthleteProfile`

Event history:

- `WorkoutLog`
- `BenchmarkObservation`

Modeled internal history:

- `AthleteState`

Planning layer:

- `MesocycleBlock`
- `PlannedSession`

Bias/inference layer:

- `WeakPoint`

Benchmark/KPI layer:

- `BenchmarkDefinition`
- `ObservationMapping`
- `DerivedMetricDefinition`
- `DerivedMetricSnapshot`

Prescription library:

- `Exercise`

## User

Represents account-level identity.

Key fields:

- `id`
- `email`
- `hashed_password`
- `is_active`
- `created_at`

Relationships:

- one `AthleteProfile`
- many `AthleteState`
- many `MesocycleBlock`
- many `WeakPoint`

Role:

The root owner for athlete-specific data.

## AthleteProfile

Represents onboarding and relatively stable baseline information.

Key fields:

- `experience_years`
- `experience_level`
- `available_days_per_week`
- `session_duration_minutes`
- `equipment`
- `squat_1rm`
- `deadlift_1rm`
- `bench_1rm`
- `overhead_1rm`
- `pullup_max_reps`
- `run_5k_seconds`
- `run_1p5mi_seconds`
- `bodyweight_kg`
- `height_cm`

Relationship:

- one-to-one with `User`

Role:

Durable configuration and baseline context. It constrains prescription and seeds assumptions.

Current caveat:

The uploaded onboarding route fills experience, schedule, and equipment. The request schema includes lift/bodyweight/run fields, and the ORM supports them, but the uploaded route does not currently assign all of those fields to the profile.

## AthleteState

Represents a persisted unified state vector `S(t)`.

Key legacy scalar fields:

- `c_met_aerobic`
- `c_nm_force`
- `c_struct`
- `b_met_anaerobic`
- `f_met_systemic`
- `f_nm_peripheral`
- `f_nm_central`
- `f_struct_damage`
- `s_struct_signal`
- `habit_strength`
- `skill_state`

Key decomposed field:

- `engine_state` JSONB with `x`, `f`, and `t`

The bridge layer keeps legacy columns and decomposed vectors aligned.

Current decomposed vectors:

`capacity_x`:

- aerobic
- glycolytic
- max_strength
- hypertrophy
- power
- skill
- mobility
- work_capacity

`fatigue_f`:

- cns
- muscular
- metabolic
- structural
- tendon
- grip

`tissue_t`:

- shoulder
- elbow
- wrist
- lumbar
- hip
- knee
- ankle
- finger

Role:

The engine's internal belief state after processing workouts or benchmark observations.

Important design choice:

Each update creates a new row. This table is a historical state timeline.

## WorkoutLog

Represents a persisted workout event created by `POST /v1/log-workout`.

Key fields:

- `user_id`
- `planned_session_id`
- `logged_at`
- `session_timestamp`
- `modality`
- `duration_minutes`
- `session_rpe`
- `avg_rir`
- `distance_meters`
- `total_volume_load`
- `sleep_quality`
- `life_stress_inverse`
- `dose_snapshot`
- `is_benchmark`
- `benchmark_results`

Role:

Observed event history. It stores the stress dose calculated at log time for auditability and future replay.

Distinction from DTO:

There is both a Pydantic `WorkoutLog` input schema and a SQLAlchemy `WorkoutLog` persistence model. Keep the distinction explicit in docs and code reviews.

## ExerciseEntry DTO

A workout log can include per-exercise entries.

Client-provided fields:

- exercise ID or name
- sets
- reps
- load
- time/distance
- RPE/RIR
- tempo
- rest

Service-resolved fields:

- phi adaptation/fatigue/tissue vectors
- energy mix
- modality
- movement pattern
- skill demand
- impact level
- recovery cost
- weak-point tags
- sport domains

These are not currently persisted as separate workout-exercise rows in the uploaded migration set. They are used to calculate dose more accurately before storing the workout's aggregate dose snapshot.

## MesocycleBlock

Represents a macro training block.

Key fields:

- `goal`
- `status`
- `duration_weeks`
- `sessions_per_week`
- `start_date`
- `end_date`
- `modality_mix`
- `weekly_template`
- `rationale`
- `deload_every_n_weeks`
- `deload_volume_factor`

Current block goals:

- Strength
- Hypertrophy
- Power
- Hyrox
- CrossFit
- Running
- Calisthenics
- General
- Recomp

Role:

Stores long-range training intent. The block defines structure; the prescriber adapts session content using the current state.

## PlannedSession

Represents a scheduled training slot inside a block.

Key fields:

- `block_id`
- `user_id`
- `scheduled_date`
- `week_number`
- `day_of_week`
- `category`
- `modality`
- `status`
- `prescribed_content`
- `workout_log_id`
- `is_deload`
- `is_benchmark`
- `benchmark_key`
- `completed_at`

Status values:

- pending
- completed
- skipped
- rescheduled

Role:

The bridge between planning and daily prescription. The exact content is generated lazily when the athlete opens the session or requests a next session.

Important design choice:

Do not overwrite the planned session with raw workout data. Link to `WorkoutLog` through `workout_log_id` / `planned_session_id`.

## WeakPoint

Represents a flagged limitation.

Key fields:

- `tag`
- `source`
- `confidence`
- `note`
- `detected_at`
- `resolved_at`
- `source_session_id`

Source values:

- self_report
- benchmark
- inference
- performance_data

Canonical weak-point tags include movement patterns, physical qualities, energy systems, and sport-specific limitations such as `grip`, `posterior_chain`, `aerobic_base`, `lactate_threshold`, `running_economy`, `barbell_technique`, and `olympic_lifting`.

Role:

Biases prescription without hijacking the main goal or safety constraints.

Benchmark feedback behavior:

- low normalized benchmark values can create or refresh benchmark-sourced weak points
- sufficiently improved normalized values can resolve matching benchmark-sourced weak points

## Exercise

Represents a movement library entry.

Key fields:

- name
- modality
- movement pattern
- pattern family
- unilateral flag
- ROM demand
- contraction bias
- primary/secondary muscles
- equipment required
- load type
- sport domains
- scalable_by
- skill demand
- technical ceiling
- impact level
- recovery cost
- novelty penalty
- `phi_adapt`
- `phi_fatigue`
- `phi_tissue`
- `energy_mix`
- weak-point tags
- benchmark flag
- coaching notes
- metadata

Role:

This is the movement library used by dose resolution and future exercise selection. It is seed/reference data, not user-owned data.

## BenchmarkDefinition

Represents a benchmark protocol or metric definition.

Key fields:

- `code`
- `name`
- `domain`
- `metric_type`
- `unit`
- `is_primary_anchor`
- `is_derived_only`
- `is_validator_only`
- `protocol_summary`
- `standardization_rules`
- `minimum_retest_interval_days`
- `better_direction`
- `observation_weight`
- `state_targets`
- `fatigue_targets`
- `tissue_targets`
- `provenance`

Role:

Defines what can be observed or computed. Some definitions are primary anchors; some are derived-only; some are validator-only.

Constraints:

A definition cannot be both primary anchor and derived-only, and cannot be both primary anchor and validator-only.

## BenchmarkObservation

Represents a user-specific benchmark result.

Key fields:

- `user_id`
- `benchmark_definition_id`
- `observed_at`
- `raw_value`
- `secondary_value`
- `normalized_value`
- `bodyweight_kg`
- `rpe`
- `heart_rate_avg`
- `heart_rate_drift_pct`
- `notes`
- `protocol_metadata`
- `validity_status`
- `source`

Role:

Observed measurement history. Valid observations can affect state through observation mappings and can update weak-point signals.

## ObservationMapping

Maps benchmark observations to state-vector changes.

Key fields:

- `benchmark_definition_id`
- `target_vector`
- `target_key`
- `mapping_type`
- `coefficient`
- `intercept`
- `min_value`
- `max_value`
- `config`

Supported mapping styles in the uploaded state update code include:

- direct
- inverse
- logistic
- ratio_threshold
- bounded

Role:

This is the current bridge from benchmark observations into state assimilation. It is not a full EKF yet, but it is a weighted residual-style nudge.

## DerivedMetricDefinition

Defines a computed KPI.

Key fields:

- `code`
- `name`
- `domain`
- `metric_type`
- `unit`
- `formula_type`
- `formula_config`
- `display_priority`
- `is_dashboard_kpi`
- `can_affect_prescriber_rules`
- `provenance`

Formula types supported in service logic:

- sum
- ratio
- weighted_sum
- custom_python_key

## DerivedMetricSnapshot

Stores a computed KPI value at a point in time.

Key fields:

- `user_id`
- `derived_metric_definition_id`
- `computed_at`
- `value`
- `confidence`
- `contributing_observation_ids`
- `notes`

Role:

A time-stamped KPI snapshot that can feed dashboards and prescriber context.

## Source of Truth by Layer

```text
User / AthleteProfile        = identity + stable setup
WorkoutLog                   = observed workout event history
BenchmarkObservation          = observed test / measurement history
AthleteState                  = modeled internal history
MesocycleBlock                = strategic plan
PlannedSession                = scheduled tactical slot
WeakPoint                     = targeted bias signals
Exercise                      = movement library
BenchmarkDefinition           = measurement taxonomy
ObservationMapping            = benchmark -> state update rule
DerivedMetricDefinition       = KPI formula definition
DerivedMetricSnapshot         = computed KPI history
```

## Typical Data Lifecycle

### 1. Account creation

Create:

- `User`
- empty `AthleteProfile`

### 2. Onboarding

Fill:

- `AthleteProfile`

Optionally create:

- self-reported `WeakPoint` rows

Create:

- baseline `AthleteState` S0

### 3. Block planning

Create:

- `MesocycleBlock`
- generated `PlannedSession` rows

The planning service can mark deload weeks and periodic benchmark sessions.

### 4. Training day

The prescriber reads:

- latest `AthleteState`
- active block/session context
- active weak points
- recent workout summaries
- KPI snapshots
- equipment/profile constraints

Then it returns `WorkoutPrescription` and may write `prescribed_content` to today's planned session.

### 5. Workout completion

Create:

- `WorkoutLog`

Then:

- compute `StressDose`
- update state
- persist new `AthleteState`
- mark planned session completed when linked or same-day matched

### 6. Benchmark observation

Create:

- `BenchmarkObservation`

Then, when valid and mappable:

- nudge state via `ObservationMapping`
- persist new `AthleteState`
- create/resolve benchmark weak points
- recompute derived KPI snapshots

## Migration Set

Current uploaded migrations:

- `a000_init` — users, profiles, exercises, mesocycle blocks, planned sessions, workout logs, weak points, athlete states
- `a001_benchmark_kpi` — benchmark definitions, observations, derived metric definitions, derived metric snapshots, observation mappings
- `a002_planned_bench_cols` — adds `is_benchmark` and `benchmark_key` to planned sessions

Run:

```bash
alembic upgrade head
```

The application startup checks the Alembic version and logs if the database is behind head.

## Conventions

### Naming

Be explicit about DTO vs ORM model names when they overlap.

Examples:

- API schema: `WorkoutLog`
- DB model: `WorkoutLogORM` alias in services

### Time

Keep these separate:

- event timestamp (`session_timestamp`)
- row creation time (`logged_at`, `created_at`)
- modeled state timestamp (`AthleteState.timestamp`)
- benchmark observation time (`observed_at`)

### Append-only state

Treat `AthleteState` rows as historical snapshots.

### Weak-point resolution

Do not delete resolved weak points. Set `resolved_at`.

### Planned vs completed

Do not overwrite planned sessions with raw completed work. Link to workout logs.

### Derived KPIs

Do not overwrite definitions. Write snapshots.

## Open Model Questions

1. How much of benchmark assimilation should remain heuristic mapping versus a fuller state-estimation system?
2. Should profile baseline lift fields be fully persisted by onboarding route?
3. Should per-exercise workout entries be persisted as first-class child rows rather than only used during dose calculation?
4. Should weak-point aggregation weight confidence/source before passing tags to the prescriber?
5. Should latest state be exposed through a cached/materialized view once state history grows?
