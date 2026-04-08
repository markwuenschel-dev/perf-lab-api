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
POST /v1/log-workout

Logs a completed workout, computes stress dose, updates the athlete state, and
returns the new S(t) snapshot.

This is the main state-transition endpoint in the current system.

Input

WorkoutLog

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
});
GET /v1/next-session

Returns the recommended next session based on the athlete’s latest state and
the requested goal.

This endpoint is the controller output of the system.

Query params
goal
Example values seen in the current UI/model surface:
Strength
Hypertrophy
Power
General

The broader block model also includes:

Running
Hyrox
CrossFit
Calisthenics
Recomp
Output

WorkoutPrescription

From current UI usage, the response is expected to include:

type
focus
duration_min
rationale

Planned session data suggests this can expand to include:

exercises
richer session structure
LLM-generated rationale
Does it mutate state?

It should be treated as read-oriented in the current model.

Its job is to read current state and produce a recommendation, not advance
the athlete model by itself.

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

UnifiedStateVector

Modeled athlete state snapshot.

Fields include:

capacities
battery
fatigue channels
adaptation signal
habit and skill information
Notes

This is the most important object in the system.
It is what the engine believes about the athlete right now.

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
GET /v1/next-session
POST /v1/simulate-dose for the same hypothetical input
Not safe to repeat casually
POST /v1/log-workout

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

The API surface is clean, but the broader platform is still mid-build.

Known boundaries from the current repo/docs:

auth exists but is not yet described here in detail
some routers are planned but not active
block, weak-point, and onboarding APIs are signposted, not fully surfaced
migrations are planned but not fully configured
some richer prescription structure is implied by models/UI comments more than
fully documented endpoint schemas

So this guide documents the currently visible contract, not a fantasy “v2”.

Invariants for API Consumers

These should remain true even as the API grows:

simulate-dose stays non-mutating
log-workout remains the main state transition endpoint
next-session remains recommendation-oriented
the client should not need to understand internal model math to consume the API
the backend remains the source of truth for S(t)