# Performance Lab API Guide

## Purpose

This guide explains how to use the Performance Lab API as an external consumer.

Swagger tells you the endpoint shapes. This document explains what each endpoint means in the system, when to call it, whether it mutates state, and what clients should avoid.

The core mental model is:

```text
workout / benchmark input -> D(t) or observation signal -> S(t) -> next session u(t)
```

Where:

- `D(t)` is the computed stress dose from a workout.
- `S(t)` is the athlete's modeled internal state.
- `u(t)` is the next recommended session.
- Benchmark observations can also nudge `S(t)` through observation mappings and update weak-point signals.

## Base Concepts

Performance Lab is not just a workout logger. It is a stateful training engine.

That means there are three different kinds of API behavior:

1. **Pure transform** — computes and returns something without changing persisted state.
2. **State transition** — changes the athlete model or related persisted history.
3. **Read-oriented with planned-session side effects** — primarily reads state, but may write generated prescription content to a planned session slot.

A client that treats all endpoints as harmless reads will create incorrect behavior.

## API Shape

Preferred backend entrypoint:

```bash
uvicorn app.main:app --reload
```

Versioned API prefix:

```text
/v1
```

Auth and legacy endpoints are outside the `/v1` prefix:

```text
/auth/*
/compute-metrics
/program/run
/program/strength
/ping
```

The FastAPI app is `app.main:app`. Schema management is handled by Alembic only; do not use `Base.metadata.create_all` as a substitute for migrations.

## Authentication

Most personalized endpoints require a Bearer token.

### POST /auth/register

Creates a new `User` and an empty `AthleteProfile` shell.

Input:

```json
{
  "email": "athlete@example.com",
  "password": "minimum-8-chars"
}
```

Password rules:

- minimum 8 characters
- maximum 72 characters because of bcrypt limits

Output:

```json
{
  "id": 1,
  "email": "athlete@example.com",
  "is_active": true
}
```

Does it mutate state?

Yes. It creates account identity and an empty profile row.

### POST /auth/token

OAuth2 password-form login endpoint.

Input uses `application/x-www-form-urlencoded`:

```text
username=athlete@example.com&password=...
```

Output:

```json
{
  "access_token": "...",
  "token_type": "bearer"
}
```

Does it mutate state?

No.

### GET /auth/me

Returns the current authenticated user.

Requires:

```text
Authorization: Bearer <token>
```

Does it mutate state?

No.

## Health and Legacy Endpoints

### GET /ping

Healthcheck endpoint.

Output includes:

```json
{
  "status": "ok",
  "system": "running",
  "version": "0.2.0",
  "project": "Performance Lab"
}
```

Does it mutate state?

No.

### POST /compute-metrics

Legacy v0.1 field-test endpoint used by the frontend's `HeroFlowColumn`.

It accepts age/sex/300m/1.5mi inputs and returns VO2 estimate, categories, fatigue profile, race pace, and pace zones.

Does it mutate state?

No.

### GET /program/run
### GET /program/strength

Legacy static program endpoints preserved for compatibility with the older field-test UI.

Does it mutate state?

No.

## Core Digital Twin Endpoints

### POST /v1/simulate-dose

Converts a workout log into a computed `StressDose`.

This endpoint answers:

> What stress would this workout create?

Input: `WorkoutLog`.

Representative fields:

```json
{
  "timestamp": "2026-05-29T15:00:00Z",
  "modality": "Strength",
  "duration_minutes": 45,
  "session_rpe": 7,
  "avg_rir": 2,
  "sleep_quality": 5,
  "life_stress_inverse": 5,
  "dominant_movement_pattern": "squat",
  "novelty": 1.0,
  "estimated_sets": 12,
  "exercises": []
}
```

`modality` currently accepts:

```text
Running | Strength | Hypertrophy | Power | Mixed
```

Optional per-exercise entries can be included. When present, the service/engine can use exercise-level phi vectors. When absent, the dose engine falls back to modality and movement-pattern defaults.

Output: `StressDose`.

Fields include:

- `dose_six.volume`
- `dose_six.intensity`
- `dose_six.density`
- `dose_six.impact`
- `dose_six.skill`
- `dose_six.metabolic`
- `adaptation_contribution` by capacity axis
- legacy channels: `d_met_systemic`, `d_nm_peripheral`, `d_nm_central`, `d_struct_damage`, `d_struct_signal`

Does it mutate state?

No.

When to use it:

- preview a session's load
- compare candidate workouts
- run what-if scenarios
- debug the input-to-dose mapping without advancing the athlete model

When not to use it:

Do not use it to record a real workout. Use `POST /v1/log-workout` instead.

### POST /v1/onboard

Creates or fills the athlete profile and seeds baseline state.

This endpoint assumes the user already exists and is authenticated. Registration creates the profile shell; onboarding fills it.

Input: `OnboardRequest`.

Fields:

- `experience_years`
- `experience_level`
- `available_days_per_week`
- `session_duration_minutes`
- `equipment`
- `self_reported_weak_points`
- `goal`
- `squat_1rm_kg`
- `deadlift_1rm_kg`
- `bench_1rm_kg`
- `bodyweight_kg`
- `run_5k_seconds`

Output: `OnboardResponse`.

```json
{
  "user_id": 1,
  "profile_id": 1,
  "message": "Athlete profile and baseline state ready.",
  "next_step": "Call GET /v1/next-session?goal=Strength to get first prescription"
}
```

Does it mutate state?

Yes. It:

1. upserts/fills `AthleteProfile`
2. creates self-reported `WeakPoint` rows when requested
3. creates baseline `AthleteState` S0

Important current behavior:

- `experience_level` chooses a baseline capacity tier.
- `squat_1rm_kg`, when present, overrides `c_nm_force` as `squat_1rm_kg * 10.0`.
- The endpoint currently stores profile fields such as experience, schedule, and equipment. The uploaded route does not show persistence of all baseline lift fields, even though they are present in the request schema.

### POST /v1/log-workout

Logs a completed workout, computes stress dose, updates athlete state, persists the workout log, links planning data when possible, and returns the new state snapshot.

Input: `WorkoutLog`.

Additional planning/benchmark fields:

- `planned_session_id`
- `is_benchmark`
- `benchmark_results`

Does it mutate state?

Yes. This is the primary state-transition endpoint.

The service flow is:

1. fetch latest `AthleteState`
2. stage baseline state if none exists
3. resolve per-exercise phi vectors from the `Exercise` table when possible
4. calculate `StressDose`
5. persist a `WorkoutLog` with `dose_snapshot`
6. link to an explicit planned session or best-effort same-day pending session
7. mark linked planned session completed
8. clamp negative elapsed time to zero
9. update `S(t)` to `S(t+1)`
10. persist a new `AthleteState` row
11. return the new `UnifiedStateVector`

Calling this endpoint twice with the same workout is not harmless. It advances the model twice and creates duplicate event/state history.

### GET /v1/next-session

Returns the recommended next session for the authenticated athlete.

Query params:

- `goal` — one of the `TrainingGoal` values
- `user_id` — current code contains a DEV ONLY override; do not expose this as a production integration pattern

Supported `goal` values:

```text
Strength
Hypertrophy
Power
General
OlympicLifts
Powerlifting
MetCon
Calisthenics
Gymnastics
Grip
Running
Sprinting
HalfMarathon
FullMarathon
```

Output: `WorkoutPrescription`.

Fields:

- `type`
- `focus`
- `rationale`
- `duration_min`
- `model_version`
- `exercises[]`
- `why`

The `why` object can include:

- `state_drivers`
- `goal_alignment`
- `constraints_applied`
- `source_alignment`
- `template_id`
- `prescription_branch`
- `validation`
- `warnings`
- `score`
- `structured_template_name`

Does it mutate state?

It does not mutate `AthleteState`. It can write generated prescription content to today's pending `PlannedSession.prescribed_content` when an active block and pending planned session exist.

Context used by the route:

- latest `AthleteState`
- auto-initialized baseline state if no state exists
- active unresolved weak-point tags
- active block and today's pending planned session
- athlete equipment from profile
- recent workout summaries
- latest KPI values from dashboard service

## Planning Endpoints

Planning endpoints are under `/v1/planning` and require auth.

### POST /v1/planning/blocks

Creates a `MesocycleBlock` and auto-generates `PlannedSession` rows.

Input: `BlockCreateRequest`.

Fields:

- `goal`
- `start_date`
- `duration_weeks`
- `sessions_per_week`
- `weekly_template`
- `modality_mix`
- `rationale`
- `deload_every_n_weeks`
- `deload_volume_factor`
- `benchmark_every_n_weeks`

If `weekly_template` is omitted, the service uses a default goal-based template for supported goals. Current defaults are explicit for Strength and Running, with fallback to Strength.

Generated sessions include:

- `week_number`
- `day_of_week`
- `category`
- `modality`
- `status = pending`
- `is_deload` based on deload cadence
- `is_benchmark` and `benchmark_key = periodic_retest` based on benchmark cadence

Does it mutate state?

Yes. It creates a block and scheduled session rows.

### GET /v1/planning/blocks

Lists the authenticated user's blocks, newest first.

Does it mutate state?

No.

### PATCH /v1/planning/blocks/{block_id}

Updates block metadata.

Currently patchable:

- `status`
- `rationale`

Does it mutate state?

Yes.

### GET /v1/planning/sessions

Lists planned sessions for the authenticated user.

Optional query params:

- `start_date`
- `end_date`

Does it mutate state?

No.

### PATCH /v1/planning/sessions/{session_id}

Updates a planned session.

Currently patchable:

- `status`
- `scheduled_date`

Does it mutate state?

Yes.

### GET /v1/planning/today

Returns today's pending planned session plus a prescription payload if state exists.

Behavior:

- Finds today's pending `PlannedSession`.
- If no session exists, returns `{ session: null, prescription: null }`.
- If no athlete state exists, returns session with `prescription: null`.
- If state exists, generates a prescription using session context and profile equipment.
- Writes prescription content back to the planned session row.

Does it mutate state?

It does not mutate `AthleteState`, but it can write `prescribed_content` to `PlannedSession`.

## Benchmark Endpoints

Benchmark endpoints are under `/v1/benchmarks` and require auth.

### GET /v1/benchmarks/definitions

Lists benchmark definitions.

Does it mutate state?

No.

### POST /v1/benchmarks/observations

Creates a benchmark observation.

Input: `BenchmarkObservationCreate`.

Fields:

- `benchmark_code`
- `raw_value`
- `secondary_value`
- `normalized_value`
- `observed_at`
- `bodyweight_kg`
- `rpe`
- `heart_rate_avg`
- `heart_rate_drift_pct`
- `notes`
- `protocol_metadata`
- `validity_status`
- `source`

Does it mutate state?

Yes, when valid and mappable. It can:

1. create a `BenchmarkObservation`
2. initialize baseline state if needed
3. apply observation mappings to create a new `AthleteState` row
4. create or refresh benchmark-sourced weak points when normalized value is low
5. resolve benchmark-sourced weak points when normalized value improves
6. recompute derived KPI metrics

Important thresholds in current service:

- normalized value `< 40.0` flags deficits
- normalized value `> 65.0` resolves matching benchmark weak points

### GET /v1/benchmarks/observations

Lists benchmark observations for the user.

Query params:

- `benchmark_code` optional
- `limit` default 100, bounded 1-500

Does it mutate state?

No.

### POST /v1/benchmarks/recompute-derived

Recomputes derived KPI snapshots from latest observations and profile context.

Does it mutate state?

Yes. It writes `DerivedMetricSnapshot` rows.

## Dashboard Endpoints

Dashboard endpoints are under `/v1/dashboard` and require auth.

### GET /v1/dashboard/kpis

Returns a bundle of latest derived KPI values and latest primary-anchor benchmark observations.

Does it mutate state?

No.

### GET /v1/dashboard/domain-summary?domain=...

Returns KPI and primary-anchor data for one domain.

Does it mutate state?

No.

### GET /v1/dashboard/readiness

Returns latest `UnifiedStateVector` plus KPI-derived readiness flags.

Current flags include:

- `run_fatigue_factor_elevated`
- `pl_relative_total_low`
- `wl_snatch_share_low`

Does it mutate state?

No.

## DTO Summary

### WorkoutLog

Raw session input.

Key fields:

- timestamp
- modality
- duration_minutes
- session_rpe
- avg_rir
- distance_meters
- total_volume_load
- dominant_movement_pattern
- novelty
- estimated_sets
- sleep_quality
- life_stress_inverse
- exercises[]
- planned_session_id
- is_benchmark
- benchmark_results

### ExerciseEntry

Optional per-exercise log entry.

Client-facing fields include:

- exercise_id
- exercise_name
- sets
- reps
- load_kg
- duration_seconds
- distance_meters
- avg_rpe
- avg_rir
- tempo
- rest_seconds

Service-resolved fields include phi vectors, energy mix, modality, movement pattern, skill demand, impact level, recovery cost, weak-point tags, and sport domains.

### StressDose

Transient transform output, not a persisted state snapshot.

Fields:

- `dose_six`
- `adaptation_contribution`
- legacy scalar channels

### UnifiedStateVector

Modeled athlete state snapshot.

Contains:

- decomposed `capacity_x`
- decomposed `fatigue_f`
- decomposed `tissue_t`
- legacy scalar mirrors
- structural signal
- habit strength
- skill state
- model version

### WorkoutPrescription

Controller output.

Contains:

- legacy display fields
- `model_version`
- exercise list
- structured explanation

## Error Handling

The frontend client expects JSON errors when available and falls back to text otherwise.

Recommended client behavior:

- treat all non-2xx responses as API errors
- surface the backend `detail` field when present
- clear session on 401 for authenticated endpoints
- do not assume every failure returns JSON

A reasonable client error shape is:

```ts
{
  message: string;
  status?: number;
  details?: unknown;
}
```

## Idempotency and Safety

Safe to repeat:

- `GET /ping`
- `GET /auth/me`
- `POST /v1/simulate-dose` for the same hypothetical input
- read/list endpoints

Read-oriented but can update planned-session content:

- `GET /v1/next-session`
- `GET /v1/planning/today`

Not safe to repeat casually:

- `POST /auth/register`
- `POST /v1/onboard`
- `POST /v1/log-workout`
- `POST /v1/planning/blocks`
- `POST /v1/benchmarks/observations`
- `POST /v1/benchmarks/recompute-derived`

## Recommended Integration Patterns

### Recommendation-driven app

```text
register/login -> onboard -> get next session -> perform workout -> log workout -> refresh next session
```

### Planner app

```text
create block -> inspect sessions -> open today's session -> perform session -> log workout -> session marked completed
```

### Benchmark loop

```text
list definitions -> post observation -> derived KPIs recomputed -> weak points/state updated -> next prescription reflects KPI + weak-point context
```

### Coach/debug console

```text
review state -> review KPIs -> review active weak points -> inspect planned vs completed sessions -> simulate alternatives -> log actual work
```

## Current Boundaries

Implemented and active:

- auth
- onboarding
- simulate dose
- log workout
- next session
- planning block/session MVP
- today planned session
- benchmark definitions/observations
- derived KPI recomputation
- dashboard KPI/readiness payloads
- exercise-aware dose fallback path
- candidate-based prescriber with validation/explainability

Not yet exposed as standalone public APIs in the uploaded source:

- weak-point create/update/resolve routes outside onboarding/benchmark feedback
- exercise library management routes
- route-level tests were not provided in the uploaded source set
