# Performance Lab Quickstart Flow

## Purpose

This guide shows the first-run lifecycle of Performance Lab from the perspective
of a developer integrating the API or a contributor trying to understand the
main control loop.

The goal is not to explain every subsystem in detail. The goal is to answer the
practical question:

> How does a new athlete move from account creation to their first adaptive
> recommendation?

At the center of the system are two service-layer functions:

- `initialize_athlete_state(...)`
- `process_new_workout(...)`

Those functions are the real backbone of the first-run experience.

---

## The Short Version

```text
Create user
   ↓
Create athlete profile (optional but strongly recommended)
   ↓
Initialize baseline state S0
   ↓
Get an initial next-session recommendation
   ↓
Log first real workout
   ↓
Update athlete state to S(t+1)
   ↓
Get the next recommendation
   ↓
Repeat loop
```

---

## What Exists Today

The currently visible API surface supports the core loop:

- `POST /v1/simulate-dose`
- `POST /v1/log-workout`
- `GET /v1/next-session`
- `GET /ping`

The repo also makes room for onboarding, blocks, and weak-point routers, but
those are signposted as planned rather than fully surfaced in the visible API
entrypoint today.

That means the onboarding story is currently a combination of:

- account creation / auth setup
- baseline model initialization
- first workout logging
- ongoing recommendation refresh

---

## Step 1: Create the User

First, create the athlete account.

At the data-model level, `User` owns the athlete-specific state, blocks, and
weak points.

Representative account fields include:

- `email`
- `hashed_password`
- `is_active`
- `created_at`

### Why this comes first

Every persisted training object hangs off `user_id`.
Without a user, there is no stable identity to attach state history to.

---

## Step 2: Create the Athlete Profile

After the user exists, create the athlete profile.

This is not just a nice-to-have form. It captures durable constraints and
baseline assumptions that should shape prescription later.

Representative profile fields include:

- experience years / level
- available days per week
- session duration
- available equipment
- baseline lifts / performance markers
- bodyweight / height

### Why it matters

The profile is where the system learns what the athlete can realistically do.
This keeps the prescriber from recommending sessions that are impossible,
mis-scoped, or mismatched to the athlete’s environment.

### Practical note

The visible service code can initialize a baseline state without a populated
profile, but production onboarding should strongly prefer creating profile data
first.

---

## Step 3: Initialize the Athlete State

Once the user exists, the system needs an initial internal state `S0`.

This is handled by:

```python
initialize_athlete_state(db, user_id)
```

### What it does

If the athlete has no prior state history, the service creates a baseline state
using safe defaults for an intermediate athlete.

The initial state includes:

- baseline capacities
- baseline anaerobic battery
- zeroed fatigue channels
- zeroed structural signal
- neutral habit strength
- starter skill values

### Why this exists

The prescriber cannot reason over a missing state.
Even if the initial values are only placeholders, the system needs a valid `S0`
so that every later update becomes a transition from a known prior state.

### Current baseline assumptions

The current service seeds values like:

- `c_met_aerobic = 300.0`
- `c_nm_force = 1000.0`
- `c_struct = 100.0`
- `b_met_anaerobic = 15000.0`
- fatigue channels set to `0.0`
- `habit_strength = 0.5`
- starter skill state for squat and deadlift

These are system defaults, not athlete-specific truth.

---

## Step 4: Get the Initial Recommendation

Once a valid `S0` exists, the client can request the next session.

Use:

```http
GET /v1/next-session?goal=Strength
```

Or any other supported goal surface such as:

- `Strength`
- `Hypertrophy`
- `Power`
- `General`

The broader model surface also makes room for additional goals such as
`Running`, `Hyrox`, `CrossFit`, `Calisthenics`, and `Recomp`.

### What to expect

The recommendation should be treated as the controller’s current best guess
based on the latest available state.

At this stage, the recommendation is being generated from baseline state rather
than a rich training history, so it should be interpreted as a starting point,
not deeply individualized guidance.

---

## Step 5: Log the First Workout

After the athlete completes a real session, log it through the ingest path.

Use:

```http
POST /v1/log-workout
```

With a body shaped like `WorkoutLog`.

Representative fields:

- `timestamp`
- `modality`
- `duration_minutes`
- `session_rpe`
- optional `avg_rir`
- optional `distance_meters`
- optional `total_volume_load`
- `sleep_quality`
- `life_stress_inverse`

### Why this step matters

This is the first real state transition.
Until this point, the athlete exists as:

- identity
- optional profile
- baseline internal state

After this step, the system also has:

- a real observed training event
- a computed stress dose
- an updated internal state derived from actual behavior

---

## Step 6: Update the State

The main state-transition service is:

```python
process_new_workout(db, user_id, log)
```

This function performs the real control-loop update.

### Internal flow

1. Fetch the latest `AthleteState`
2. Initialize `S0` if no state exists yet
3. Compute `D(t)` from the incoming `WorkoutLog`
4. Compute elapsed time `dt`
5. Apply the state update rules
6. Persist a **new** athlete state row
7. Return the updated `UnifiedStateVector`

### Important behavior

If the incoming workout timestamp is older than the current state timestamp,
`dt` is clamped to zero instead of allowing a negative time transition.

That is an important safety behavior and should remain documented.

---

## Step 7: Refresh the Next Session

After the first workout is logged and the state is updated, request the next
session again.

This is the first moment where the recommendation is meaningfully shaped by:

- a real workout event
- computed stress dose
- updated fatigue state
- current goal

In practical client flow:

1. get recommendation
2. complete session
3. log workout
4. get recommendation again

That is already how the current frontend behaves.

---

## Step 8: Repeat the Loop

Once the first workout has been processed, the system enters its normal cycle.

```text
recommend → perform → log → update state → recommend again
```

That loop is the core product experience.

As more infrastructure is added, the same loop can be enriched by:

- better onboarding data
- active mesocycle blocks
- planned session slots
- weak-point inference
- benchmark sessions
- individualized model calibration

But the loop itself does not change.

---

## Example End-to-End Narrative

Here is the practical first-run story for a new athlete.

### Day 0
- user account is created
- athlete profile is created
- baseline state `S0` is initialized
- client asks for `next-session`
- athlete sees an initial recommendation

### Day 1
- athlete completes the recommended session
- client sends `POST /v1/log-workout`
- server computes `D(t)`
- server updates the state to `S(t+1)`
- client requests `next-session` again
- athlete sees an updated prescription

### Day 2+
- the same loop repeats
- recommendations become progressively more grounded in actual training history

---

## Minimal Integration Sequence

A minimal real integration can be thought of in four calls.

### 1. Ensure the API is alive
```http
GET /ping
```

### 2. Ensure the athlete exists and has baseline state
This may involve auth/user creation plus a service call or onboarding path.

### 3. Get the first recommendation
```http
GET /v1/next-session?goal=Strength
```

### 4. After the workout, log it
```http
POST /v1/log-workout
```

Then request `next-session` again.

---

## Common Mistakes to Avoid

## Mistake 1: Treating `log-workout` like a harmless form submit
It is not just persistence. It advances the athlete model.
Duplicate submissions are not benign.

## Mistake 2: Skipping baseline state creation
The system should always have a valid `S0` before trying to reason about the
athlete.

## Mistake 3: Confusing `simulate-dose` with `log-workout`
`simulate-dose` is for previews and experiments.
`log-workout` is for real completed work.

## Mistake 4: Expecting deep personalization before any history exists
The first recommendation is useful, but it is still operating from sparse
context. True adaptation starts after real logged sessions accumulate.

---

## Suggested Future Onboarding API Shape

As onboarding becomes explicit, a strong flow would be:

1. create user
2. create athlete profile
3. optionally record self-reported weak points
4. initialize baseline state
5. optionally generate a starting block
6. return first recommendation

That would turn the current architectural pieces into a clean first-run API.

---

## Short Version

Performance Lab starts simple:

```text
Create user
→ initialize state
→ get recommendation
→ log workout
→ update state
→ get next recommendation
→ repeat
```

The two service functions that matter most to this flow are:

- `initialize_athlete_state(...)`
- `process_new_workout(...)`

That is the first-run narrative the repo currently needs.
