# Performance Lab API Guide

## Purpose

This guide explains how to use the Performance Lab API as an external consumer.

Swagger tells you the shape of each endpoint. This document explains the
meaning of each endpoint in the system:

- what it is for
- when to call it
- what it changes
- what it does **not** change

The core mental model is:

```text
log/test input → D(t) → S(t) → next session u(t)

Where:

D(t) is the computed stress dose from a workout
S(t) is the athlete’s current modeled internal state
u(t) is the next recommended session
Base Concepts

Performance Lab is not just a workout logger.

It is a stateful training engine. That means some endpoints are pure transforms
and some endpoints move the athlete model forward.

That distinction matters.

Pure endpoint

A pure endpoint computes something and returns it, but does not change stored state.

Mutating endpoint

A mutating endpoint updates the persisted athlete model and should be treated as
a state transition.

If a client ignores that difference, it will create incorrect behavior very fast.

API Shape

Preferred backend entrypoint:

uvicorn app.main:app --reload

Default API version prefix:

/v1

Utility endpoint outside the versioned prefix:

/ping
Current Endpoints
GET /ping

Healthcheck endpoint.

Use this to verify that the API is reachable.

Response

Returns a small status payload indicating the system is running.

Use cases
deployment smoke test
client boot check
environment verification
Does it mutate state?

No.

POST /v1/simulate-dose

Converts a workout log into a computed StressDose.

This endpoint is the safest way to answer:

“What stress would this workout create?”

Input

WorkoutLog

Typical fields include:

timestamp
modality
duration
session RPE
optional RIR
optional distance
optional volume
sleep quality
inverse life stress
Output

StressDose

Current stress dose fields:

d_met_systemic
d_nm_peripheral
d_nm_central
d_struct_damage
d_struct_signal
Does it mutate state?

No.

When to use it

Use this endpoint when you want to:

preview a session’s load
run what-if scenarios
compare candidate workouts
inspect the input-to-dose mapping without advancing the athlete state
When not to use it

Do not use this endpoint when the workout actually happened and should count
toward the athlete’s training history.

Use POST /v1/log-workout for that.

Client example
const dose = await simulateDose({
  timestamp: new Date().toISOString(),
  modality: "Strength",
  duration_minutes: 45,
  session_rpe: 7,
  avg_rir: 2,
  sleep_quality: 5,
  life_stress_inverse: 5,
});
POST /v1/onboard

Creates the athlete profile and seeds baseline state in a single call.

This endpoint is the correct entry point for a new athlete.

Input

OnboardRequest

Fields include:

email (required)
experience_level ("beginner" | "intermediate" | "advanced" | "elite")
experience_years
available_days_per_week
session_duration_minutes
equipment
self_reported_weak_points (list of canonical weak-point tags)
squat_1rm_kg
deadlift_1rm_kg
bench_1rm_kg
bodyweight_kg
run_5k_seconds

All fields except `email` are optional. If `squat_1rm_kg` is provided, it is used
to seed `c_nm_force = squat_1rm_kg * 10.0` directly. Otherwise, `experience_level`
drives a 4-tier baseline table (beginner → elite).

Output

OnboardResponse

user_id
profile_id
message

Does it mutate state?

Yes.

This endpoint:

creates the AthleteProfile row
optionally creates self-reported WeakPoint rows
initializes baseline AthleteState S0 immediately

After this call, GET /v1/next-session will return a recommendation without
needing to auto-initialize state separately.

When to use it

Use this endpoint once per athlete, immediately after account creation. All
subsequent state evolution happens through POST /v1/log-workout.

Client example
const onboardResult = await onboard({
  email: "athlete@example.com",
  experience_level: "intermediate",
  squat_1rm_kg: 100,
  available_days_per_week: 4,
  goal: "Strength",
});

POST /v1/log-workout

Logs a completed workout, computes stress dose, updates the athlete state, and
returns the new S(t) snapshot.

This is the main state-transition endpoint in the current system.

Input

WorkoutLog

Additional planning-aware fields now supported:
- `planned_session_id` (optional)
- `is_benchmark` (optional)
- `benchmark_results` (optional key/value payload)

Output

UnifiedStateVector

Current state fields include:

capacities:
c_met_aerobic
c_nm_force
c_struct
battery:
b_met_anaerobic
fatigues:
f_met_systemic
f_nm_peripheral
f_nm_central
f_struct_damage
signals:
s_struct_signal
human factors:
habit_strength
skill_state
Does it mutate state?

Yes.

This endpoint:

loads the latest athlete state
initializes a baseline state if none exists
computes D(t) from the workout log
computes the time delta since the previous state
updates the model
persists a new state row
persists a `WorkoutLog` event row
links to a planned session when explicitly provided or same-day pending match exists
marks linked planned session as completed
returns the new state snapshot
Why that matters

Calling this endpoint twice with the same workout is not harmless.
It is not just “saving a form.” It advances the athlete model.

When to use it

Use this endpoint when:

a workout actually occurred
the engine should treat it as real training history
the next prescription should reflect it
When not to use it

Do not use this endpoint for:

experimentation
previewing candidate sessions
debugging dose logic in isolation

Use simulate-dose for that.

Client example
const newState = await logWorkout({
  timestamp: new Date().toISOString(),
  modality: "Running",
  duration_minutes: 35,
  session_rpe: 6,
  distance_meters: 6000,
  sleep_quality: 7,
  life_stress_inverse: 6,
  planned_session_id: 42,
});

GET /v1/planning/blocks
POST /v1/planning/blocks
PATCH /v1/planning/blocks/{block_id}

Planning block CRUD for authenticated users.

Use these endpoints to create/update the mesocycle container and auto-generate
planned sessions.

GET /v1/planning/sessions
PATCH /v1/planning/sessions/{session_id}

Session list/update surface.

- list supports date-window filtering (`start_date`, `end_date`)
- patch supports status transitions and rescheduling date updates

GET /v1/planning/today

Returns today’s pending planned session slot (if any) plus current prescription
context for the selected goal.

This is the preferred endpoint for “what slot should I execute today?” UX.
GET /v1/next-session

Returns the recommended next session based on the athlete’s latest state and
the requested goal.

This endpoint is the controller output of the system.

Query params
goal
Supported values:
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

Output

WorkoutPrescription

type
focus
rationale
duration_min
model_version ("v0.3")
exercises (list of ExercisePrescription — name, sets, reps, load_note, weak_point_tags)
why (PrescriptionExplanation — state_drivers, goal_alignment, constraints_applied, warnings, score)

Side effects

This endpoint:

auto-initializes baseline AthleteState if none exists (prefer POST /v1/onboard instead)
queries active unresolved WeakPoint rows and passes them to the prescriber as
  bias signals (shown as weak_point:{tag} entries in constraints_applied)
queries AthleteProfile equipment and injects available equipment context
queries the active MesocycleBlock and today’s PlannedSession if one exists,
  applies a +0.15 score bias to candidates matching the planned session category,
  and writes the resulting prescription back to PlannedSession.prescribed_content

Does it mutate state?

It does not mutate AthleteState. However, if an active planned session exists
for today, it will write the generated prescription to that row. Treat it as
read-oriented for state purposes, but be aware of the planned-session side effect.

When to use it

Use this endpoint when you want:

the next recommended workout
refreshed guidance after logging a session
a recommendation for a selected training goal
Common pattern

A normal client flow is:

call GET /v1/next-session
user completes workout
call POST /v1/log-workout
call GET /v1/next-session again

That is exactly how the current frontend uses it.

Client example
const rx = await getNextSession("Strength");
Current Client Flow

The current frontend expresses the intended API usage very clearly.

On initial load

The UI requests a next session for the selected goal.

On “Simulate D(t)”

The UI calls POST /v1/simulate-dose and shows the returned dose only.

On “Log & update S(t)”

The UI calls POST /v1/log-workout, stores the returned state, then immediately
refreshes the recommended next session.

On “Refresh u(t)”

The UI calls GET /v1/next-session again.

This is the correct separation of concerns:

simulate for preview
log for mutation
next-session for control output
Current DTOs
OnboardRequest

email
experience_level ("beginner" | "intermediate" | "advanced" | "elite", default "intermediate")
experience_years
available_days_per_week
session_duration_minutes
equipment
self_reported_weak_points
goal
squat_1rm_kg, deadlift_1rm_kg, bench_1rm_kg, bodyweight_kg, run_5k_seconds (all optional)

OnboardResponse

user_id
profile_id
message
next_step

WorkoutLog

Raw workout input.

Representative fields:

timestamp
modality
duration_minutes
session_rpe
avg_rir
distance_meters
total_volume_load
sleep_quality
life_stress_inverse

Notes
session_rpe is bounded
human context is part of the input, not an afterthought
the same DTO is used for both simulation and logging

StressDose

Internal dose vector returned by simulation.

Fields:

d_met_systemic
d_nm_peripheral
d_nm_central
d_struct_damage
d_struct_signal
Notes

This is not a user profile and not a persisted state snapshot.
It is a transient internal transform output.

WorkoutPrescription

type
focus
rationale
duration_min
model_version (e.g. "v0.3" — identifies the engine version that produced this)
exercises (list of ExercisePrescription — name, sets, reps, load_note, weak_point_tags)
why (PrescriptionExplanation — state_drivers, goal_alignment, constraints_applied, warnings, score)

Notes

constraints_applied contains weak_point:{tag} entries for any active unresolved
weak points that were passed to the prescriber at generation time.

UnifiedStateVector

Modeled athlete state snapshot.

Fields include:

capacities (c_met_aerobic, c_nm_force, c_struct, b_met_anaerobic)
fatigue channels (f_met_systemic, f_nm_peripheral, f_nm_central, f_struct_damage)
adaptation signal (s_struct_signal)
habit and skill information (habit_strength, skill_state)
model_version (e.g. "v0.3" — identifies which engine version produced this state)
decomposed vectors (capacity_x, fatigue_f, tissue_t)

Notes

This is the most important object in the system.
It is what the engine believes about the athlete right now.
model_version allows future consumers to detect which formula set produced
each persisted state row.

Error Handling

The current frontend client expects JSON error payloads when available and falls
back to text otherwise.

Recommended client behavior:

treat non-2xx responses as structured API errors
surface the backend detail field when present
do not assume all failures return JSON

A reasonable client error shape is:

message
status
details
Idempotency and Safety

This is the main place API consumers will make mistakes.

Safe to repeat
GET /ping
POST /v1/simulate-dose for the same hypothetical input

Has a side effect (planned-session write) but does not mutate AthleteState
GET /v1/next-session
  - if a PlannedSession exists for today, the generated prescription is written
    back to planned_session.prescribed_content; repeated calls overwrite it with
    the same content, so this is safe to call multiple times

Not safe to repeat casually
POST /v1/log-workout
POST /v1/onboard (creates a new profile row each call — call once per athlete)

Why:
log-workout advances the stored model and should be treated like recording
a real event, not like refreshing a page.

If duplicate submission protection becomes necessary later, it should be added
deliberately rather than assumed.

Recommended Integration Patterns
Pattern 1: Recommendation-driven app

Use when the product is centered on “what should I do today?”

Fetch recommendation
Show rationale
User completes workout
Log workout
Refresh recommendation
Pattern 2: Preview / planner app

Use when the product helps compare hypothetical sessions.

Build candidate workouts
Simulate dose for each
Compare outcomes
Log only the chosen completed workout
Pattern 3: Coach console

Use when a coach wants to inspect state and decisions.

Review current state
Review recommended session
Simulate alternatives if needed
Log actual completed work
Re-check next session
Current Limitations

The API surface is clean and the core loop is fully operational as of v0.3.

Known boundaries:

auth exists but is not yet described here in detail
block creation and calendar-generation routes are not yet active
  (MesocycleBlock + PlannedSession are readable by the prescriber if rows exist,
  but there is no public API to create or manage blocks yet)
exercise library seed data is not fully populated across all modalities
data assimilation / EKF correction is planned but not implemented
some frontend sections are demo placeholders pending block/history views

The following are now implemented and stable:

POST /v1/onboard (profile creation + baseline state seeding)
GET /v1/next-session with weak-point and block-context injection
Alembic migrations a000 (foundational tables) + a001 (benchmark KPI tables)
model_version traceability on UnifiedStateVector and WorkoutPrescription

Invariants for API Consumers

These should remain true even as the API grows:

simulate-dose stays non-mutating
log-workout remains the main state transition endpoint
next-session remains recommendation-oriented
the client should not need to understand internal model math to consume the API
the backend remains the source of truth for S(t)
