# Performance Lab Quickstart Flow

## Purpose

This guide explains the first-run lifecycle of Performance Lab from account creation through the first adaptive recommendation, first workout, planning block, and benchmark loop.

The practical question is:

> How does a new athlete move from account creation to useful adaptive training guidance?

## The Short Version

```text
Register user
   ↓
Login and store token
   ↓
Onboard athlete profile + seed baseline state S0
   ↓
Create optional planning block
   ↓
Get first next-session recommendation or today's planned session
   ↓
Log completed workout
   ↓
Persist workout + update S(t+1) + complete linked planned session
   ↓
Refresh next-session recommendation
   ↓
Optionally post benchmark observations
   ↓
Update KPIs / weak points / state
   ↓
Repeat
```

## What Exists Today

The core loop is implemented:

```text
POST /auth/register
POST /auth/token
GET  /auth/me
POST /v1/onboard
GET  /v1/next-session
POST /v1/simulate-dose
POST /v1/log-workout
GET  /v1/next-session
```

Planning is implemented:

```text
POST  /v1/planning/blocks
GET   /v1/planning/blocks
PATCH /v1/planning/blocks/{block_id}
GET   /v1/planning/sessions
PATCH /v1/planning/sessions/{session_id}
GET   /v1/planning/today
```

Benchmark/dashboard loop is implemented:

```text
GET  /v1/benchmarks/definitions
POST /v1/benchmarks/observations
GET  /v1/benchmarks/observations
POST /v1/benchmarks/recompute-derived
GET  /v1/dashboard/kpis
GET  /v1/dashboard/domain-summary
GET  /v1/dashboard/readiness
```

## Step 0: Run the Backend Correctly

The preferred entrypoint is:

```bash
uvicorn app.main:app --reload
```

Before starting against a real database, run:

```bash
alembic upgrade head
```

The app checks database connectivity and Alembic head at startup. Schema management is migration-based; do not rely on `create_all`.

## Step 1: Register the User

```http
POST /auth/register
```

Input:

```json
{
  "email": "athlete@example.com",
  "password": "strong-password"
}
```

Registration creates:

- `User`
- empty `AthleteProfile`

Why this comes first:

Every athlete-specific row hangs off `user_id`.

## Step 2: Login

```http
POST /auth/token
```

Use OAuth2 password form data:

```text
username=athlete@example.com&password=strong-password
```

Store the returned Bearer token. The frontend stores it in `sessionStorage`.

## Step 3: Confirm Current User

```http
GET /auth/me
Authorization: Bearer <token>
```

This verifies the token and returns the current user.

## Step 4: Onboard the Athlete

```http
POST /v1/onboard
Authorization: Bearer <token>
```

Input example:

```json
{
  "experience_level": "intermediate",
  "experience_years": 4,
  "available_days_per_week": 4,
  "session_duration_minutes": 60,
  "equipment": ["barbell", "dumbbells", "pullup_bar"],
  "self_reported_weak_points": ["grip", "posterior_chain"],
  "goal": "Strength",
  "squat_1rm_kg": 120,
  "bodyweight_kg": 82
}
```

What it does:

1. finds or creates the `AthleteProfile`
2. fills experience, schedule, and equipment fields
3. creates self-reported weak-point rows
4. initializes baseline `AthleteState` S0

Baseline seeding uses a four-tier table:

| Level | c_met_aerobic | c_nm_force | c_struct | b_met_anaerobic |
|---|---:|---:|---:|---:|
| beginner | 180 | 500 | 60 | 8000 |
| intermediate | 300 | 1000 | 100 | 15000 |
| advanced | 500 | 1800 | 160 | 25000 |
| elite | 650 | 2500 | 220 | 35000 |

If `squat_1rm_kg` is provided, `c_nm_force = squat_1rm_kg * 10.0`.

Current baseline state also starts with:

- zero fatigue vectors
- zero tissue vectors
- `habit_strength = 0.5`
- starter skill state for squat and deadlift

## Step 5: Get the First Recommendation

```http
GET /v1/next-session?goal=Strength
Authorization: Bearer <token>
```

The route reads:

- latest state
- profile equipment
- active weak points
- active block/today session if present
- recent workout summaries
- latest KPI values

If no state exists, it auto-initializes a baseline state.

The response is a `WorkoutPrescription` with:

- type
- focus
- rationale
- duration
- exercise list
- structured explanation

## Step 6: Optionally Create a Planning Block

```http
POST /v1/planning/blocks
Authorization: Bearer <token>
```

Input example:

```json
{
  "goal": "Strength",
  "start_date": "2026-05-29",
  "duration_weeks": 8,
  "sessions_per_week": 3,
  "deload_every_n_weeks": 4,
  "benchmark_every_n_weeks": 4
}
```

The service creates:

- one active `MesocycleBlock`
- scheduled `PlannedSession` rows
- deload flags based on cadence
- periodic benchmark session flags based on cadence

If no weekly template is supplied, the service uses built-in defaults for Strength or Running, with Strength as fallback.

## Step 7: Open Today's Planned Session

```http
GET /v1/planning/today?goal=Strength
Authorization: Bearer <token>
```

Behavior:

- returns today's pending planned session if one exists
- returns a prescription if state exists
- writes prescription content back to the planned session

This is the best endpoint for a planning-first UI.

## Step 8: Simulate a Workout Before Logging

```http
POST /v1/simulate-dose
```

This endpoint is pure. It returns `StressDose` and does not create state or logs.

Use it to preview or compare candidate sessions.

## Step 9: Log the Completed Workout

```http
POST /v1/log-workout
Authorization: Bearer <token>
```

Input example:

```json
{
  "timestamp": "2026-05-29T18:30:00Z",
  "modality": "Strength",
  "duration_minutes": 60,
  "session_rpe": 7.5,
  "avg_rir": 2,
  "sleep_quality": 7,
  "life_stress_inverse": 6,
  "dominant_movement_pattern": "squat",
  "estimated_sets": 15,
  "planned_session_id": 42
}
```

The service:

1. fetches latest state
2. initializes baseline if absent
3. resolves exercise phi vectors if exercise IDs/names are present
4. calculates stress dose
5. stores workout event with dose snapshot
6. links to explicit planned session or same-day pending session
7. marks planned session completed
8. clamps negative `dt` to zero
9. updates state
10. persists a new state row
11. returns the latest state

## Step 10: Refresh the Recommendation

After logging:

```http
GET /v1/next-session?goal=Strength
```

Now the recommendation reflects the completed workout, current fatigue, updated tissue stress, active weak points, KPIs, and block context.

## Step 11: Post Benchmark Observations

First list definitions:

```http
GET /v1/benchmarks/definitions
```

Then post an observation:

```http
POST /v1/benchmarks/observations
```

Example:

```json
{
  "benchmark_code": "run_5k",
  "raw_value": 1320,
  "normalized_value": 55,
  "observed_at": "2026-05-29T12:00:00Z",
  "validity_status": "valid",
  "source": "manual"
}
```

If valid and mappable, this can:

- create a benchmark observation
- apply observation mappings to state
- create a new `AthleteState`
- create or resolve benchmark weak points
- recompute derived KPI snapshots

## Step 12: Read Dashboard / Readiness

```http
GET /v1/dashboard/kpis
GET /v1/dashboard/readiness
```

The dashboard surfaces:

- derived KPI snapshots
- primary anchor observations
- latest state
- KPI-derived flags

These same KPI values can feed prescription logic.

## Normal Client Flow

### Non-planning flow

```text
register -> login -> onboard -> get next-session -> simulate optional -> log workout -> get next-session
```

### Planning flow

```text
register -> login -> onboard -> create block -> get planning/today -> log workout -> completed session -> get next/today again
```

### Benchmark loop

```text
list benchmark definitions -> post observation -> KPIs recomputed -> weak points/state updated -> prescription reflects new context
```

## Common Mistakes to Avoid

### Mistake 1: Treating `log-workout` like a harmless form submit

It advances the model and creates a workout row. Duplicate submissions are not benign.

### Mistake 2: Using `simulate-dose` for real completed work

Simulation does not persist history or update state.

### Mistake 3: Skipping baseline state creation and expecting deep recommendations

`next-session` can auto-initialize, but onboarding gives better first-run state.

### Mistake 4: Expecting planned sessions to be workout logs

Planned sessions are schedule slots. Workout logs are observed events. Link them.

### Mistake 5: Assuming benchmark observations are just dashboard data

Valid benchmark observations can update state and weak-point signals.

### Mistake 6: Forgetting auth prefix rules

Auth is not under `/v1`; training routes are.

## Minimal Working Sequence

```bash
# 1. Migrate database
alembic upgrade head

# 2. Start backend
uvicorn app.main:app --reload

# 3. Register user
POST /auth/register

# 4. Login
POST /auth/token

# 5. Onboard
POST /v1/onboard

# 6. Get first session
GET /v1/next-session?goal=Strength

# 7. Log completed work
POST /v1/log-workout

# 8. Refresh guidance
GET /v1/next-session?goal=Strength
```
