# Performance Lab Testing Strategy

## Purpose

This document explains what should be tested in Performance Lab, why those tests
matter, and which behaviors deserve strict assertions versus looser tolerance.

The project is not a typical CRUD API.
It is a stateful control system with:

- internal model transitions
- approximate physiological mappings
- adaptive prescription decisions
- persistence of event and state history

That means shallow route tests are not enough.

---

## Testing Philosophy

The central question is not:

> “Does the endpoint return 200?”

The central question is:

> “Did the engine behave correctly, safely, and consistently under realistic
> training conditions?”

A good test suite should therefore separate:

- **hard invariants** that must not break
- **bounded outputs** that must stay in valid ranges
- **approximate calculations** that may evolve without implying a bug
- **scenario behavior** that only appears across sequences of workouts

---

## Test Pyramid for This Project

A useful testing stack for Performance Lab is:

### 1. Unit tests
Fast tests for:
- dose calculation
- state update rules
- prescriber selection rules
- helper functions

### 2. Integration tests
Tests across:
- DB session + service layer
- route + schema + service wiring
- persistence and retrieval of latest state

### 3. Scenario tests
Longer-flow tests for:
- repeated heavy training
- deload behavior
- missed sessions
- weak-point emergence
- recommendation shifts over time

### 4. Contract tests
Tests that keep the public API stable:
- response shape
- required fields
- error handling
- mutating vs non-mutating behavior

---

## What Must Never Break

These are the highest-priority invariants.
If these break, the model is no longer trustworthy.

## 1. State transition integrity
A logged workout must produce a valid transition from prior state to next state.

Tests should verify:
- the latest state is fetched correctly
- baseline state is created when no prior state exists
- a new state row is persisted after update
- old state history is preserved

### Why this matters
This is the backbone of the engine.
If state history is lost or overwritten, replayability and trust collapse.

---

## 2. Append-only state history
`AthleteState` should behave like a history table, not a mutable singleton.

Tests should verify:
- logging a workout creates a new state row
- the previous row remains intact
- latest-state lookup returns the newest timestamped row

### Failure mode
A “helpful simplification” that overwrites the prior row would destroy time-series history.

---

## 3. Fatigue bounds
Fatigue channels should remain in valid numeric ranges.

Relevant fields:
- `f_met_systemic`
- `f_nm_peripheral`
- `f_nm_central`
- `f_struct_damage`

Tests should verify:
- fatigue values never become negative
- fatigue values remain within intended schema bounds
- edge-case workouts do not produce invalid numeric states

### Why this matters
Out-of-range fatigue values tend to cascade into broken recommendations.

---

## 4. Monotonicity and time handling rules
Time should never run backward in state evolution.

The current service already clamps negative elapsed time to zero when a workout
arrives with an older timestamp than the current state.

Tests should verify:
- negative `dt` never propagates
- out-of-order workout timestamps do not corrupt the transition
- timestamp ordering remains consistent for persisted state history

### Why this matters
Temporal bugs in a stateful model are subtle and destructive.

---

## 5. Mutating vs non-mutating endpoint separation
The API distinguishes between preview and state transition.

Tests should verify:
- `POST /v1/simulate-dose` does **not** create or mutate athlete state
- `POST /v1/log-workout` **does** create a new state row
- `GET /v1/next-session` behaves as read-oriented in the current design

### Why this matters
If preview paths mutate state, the control loop becomes untrustworthy.

---

## What Is Approximate by Nature

These are areas where exact numeric matching should usually be avoided.

## 1. Dose calculations
The dose engine is a modeling layer, not a bookkeeping layer.

That means tests should usually assert:
- directional behavior
- monotonic trends
- reasonable output ranges
- stable relationships between input severity and dose magnitude

Not brittle exact magic numbers, unless you are explicitly snapshotting a chosen
reference implementation.

### Good test style
- a harder workout produces more systemic dose than an easier one
- poor sleep increases cost relative to otherwise matched input
- adding distance or volume changes the relevant dose channels

### Bad test style
- asserting that a workout always produces exactly `17.38291` forever

---

## 2. Prescriber scoring and ranking
As prescriber logic evolves, exact ordering may change while still being correct.

Tests should favor:
- exclusion rules
- required constraints
- acceptable recommendation classes
- rationale presence

Instead of locking the system into one exact implementation too early.

---

## What Requires Scenario Tests

Some behaviors only show up over sequences, not single function calls.

## 1. Overreaching / overtraining patterns
The engine should respond differently after repeated hard sessions than after one.

Scenario tests should simulate:
- multiple high-RPE sessions in close succession
- inadequate recovery between sessions
- worsening sleep or life stress inputs

Expected outcomes may include:
- accumulating fatigue
- reduced readiness
- more conservative recommendations
- lower-cost next sessions

### Why this matters
A single isolated workout test cannot tell you whether the system behaves like a
real control loop.

---

## 2. Deload cycles
The schema already makes room for deload behavior via block-level configuration
and `PlannedSession.is_deload`.

Scenario tests should verify:
- deload sessions preserve block identity while reducing dosage
- intensity / volume targets are reduced appropriately
- the system does not treat deload weeks as random unrelated sessions

### Why this matters
Many systems claim to support deloads but only by switching to generic light workouts.

---

## 3. First-run lifecycle
The first-run path deserves its own scenario test.

Test:
- create a user
- initialize baseline state
- request next session
- log first workout
- verify updated state exists
- request next session again

This should mirror the real adoption path.

---

## 4. Planned vs completed session behavior
As planning routes are added, scenario tests should verify:
- a planned session can be fulfilled by a workout log
- completed status is tracked correctly
- skipped or rescheduled sessions do not silently disappear

---

## 5. Weak-point emergence and resolution
Once weak-point logic is more exposed, scenario tests should verify:
- multiple weak-point signals can accumulate for the same tag
- active unresolved weak points affect prescription biasing
- resolved weak points stop biasing recommendations

---

## Recommended Test Categories

## A. Dose engine tests
Focus:
- relative scaling
- modality-specific behavior
- human-factor effects
- valid ranges

Examples:
- running dose differs from strength dose for matched duration/RPE
- worse sleep increases fatigue-related dose pressure
- higher RPE generally raises stress dose channels

---

## B. State update tests
Focus:
- persistence of new state rows
- decay/adaptation behavior
- range safety
- timestamp handling

Examples:
- first workout initializes baseline if absent
- second workout uses the latest existing state
- negative elapsed time is clamped safely
- fatigue channels remain valid

---

## C. Service-layer tests
Focus:
- orchestration correctness in `initialize_athlete_state(...)`
- orchestration correctness in `process_new_workout(...)`

Examples:
- `initialize_athlete_state` creates only one baseline row when needed
- `process_new_workout` calls dose calculation and persists a new state
- state history length increases after logging a workout

---

## D. API route tests
Focus:
- request/response shape
- route wiring
- serialization
- non-mutating vs mutating path behavior

Examples:
- `POST /v1/simulate-dose` returns a valid `StressDose`
- `POST /v1/log-workout` returns a valid `UnifiedStateVector`
- `GET /ping` returns health payload
- invalid workout payloads return validation errors

---

## E. Prescriber tests
Focus:
- constraint obedience
- recommendation shape
- rationale generation
- correct response to state conditions

Examples:
- high structural fatigue does not yield a high-impact recommendation class
- equipment constraints eliminate unsupported exercise choices
- output includes `type`, `focus`, `duration_min`, and `rationale`

---

## Assertion Style Guidelines

## Prefer invariant assertions for core safety
Example:
- state row count increased by one
- fatigue remains within bounds
- no negative time delta is applied

## Prefer directional assertions for physiological logic
Example:
- harder session yields greater systemic stress than easier session

## Prefer class-based assertions for recommendations
Example:
- recommendation belongs to an acceptable low-cost category
instead of demanding one exact string too early

## Avoid over-snapshotting approximate systems
Snapshot tests can be useful for payload shape and rationale presence, but they
should not freeze every numeric modeling detail unless that is intentional.

---

## Suggested Test Matrix

A compact initial matrix could be:

### Core invariants
- baseline initialization
- append-only state history
- fatigue range safety
- non-negative effective `dt`

### API contracts
- simulate-dose does not mutate
- log-workout mutates and persists
- next-session returns valid response shape

### Scenarios
- first-run flow
- repeated hard sessions
- recovery-friendly session after overload
- deload week session behavior

That would already be far more useful than a suite made of only happy-path route tests.

---

## Current Test Coverage (v0.3)

The following test files are in place:

### Unit tests (no DB required)
`tests/test_dose_engine_v0.py` — 10 tests covering:
- return type (StressDose)
- all channels non-negative
- higher RPE increases dose
- running produces aerobic-dominant adaptation
- strength produces max_strength adaptation
- non-mutating (two calls → identical results)
- near-zero duration → near-zero dose

`tests/test_state_update_unit.py` — 10 tests covering:
- return type (UnifiedStateVector)
- append-only (prior state unchanged)
- fatigue increases after high RPE
- high fatigue suppresses adaptation (tested at fatigue=100)
- capacity not decreased by normal workout
- zero timedelta still applies dose
- legacy mirror consistency (c_nm_force == capacity_x.max_strength * 10.0)

### DB integration tests (requires live PostgreSQL)
`tests/conftest.py` — async DB fixture using `DROP SCHEMA public CASCADE`
isolation (handles circular FK between planned_sessions and workout_logs)

`tests/test_orm_persistence.py` — 5 tests covering:
- process_new_workout appends one new row
- prior state row not modified
- new state timestamp matches log timestamp
- workout_log is separate from athlete_state (Decision 2)
- simulate_dose creates no state row

`tests/test_integration_flow.py` — 4 scenario tests:
- first run: new user → init → workout → fatigue > baseline
- repeated sessions: 3 logs → 4 rows, timestamps ascending
- backdated log: timestamp before state → dt clamped to 0, no crash
- fatigue accumulates: two hard sessions close together → f2 > f1

### Remaining test targets
- route tests for `POST /v1/log-workout` and `GET /v1/next-session`
- deload and weak-point scenario tests
- `POST /v1/onboard` end-to-end test

---

## What Should Be Tested Next

If extending coverage, the priority order is:

1. `POST /v1/onboard` — profile creation + baseline state in one call
2. `GET /v1/next-session` — shape, model_version, exercises, why
3. `POST /v1/log-workout` — route-level wiring
4. repeated-session overload scenarios
5. deload and weak-point scenarios

---

## Short Version

Performance Lab should test four things differently:

### Must never break
- state history behavior
- fatigue bounds
- time-handling invariants
- mutating vs non-mutating endpoint separation

### Approximate by nature
- dose calculations
- recommendation ranking details

### Needs scenario tests
- overreaching / overtraining patterns
- deload cycles
- first-run flow
- weak-point accumulation

That is the difference between testing a website and testing a training engine.
