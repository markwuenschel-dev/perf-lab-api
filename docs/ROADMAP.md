# Performance Lab Roadmap

## Purpose

This roadmap makes the project’s near-term and medium-term direction explicit.

It exists to answer three questions:

1. what is already present
2. what is clearly planned
3. what should come next to make the system feel complete

This is not a promise of dates.
It is a map of architectural priorities.

---

## Current Position

Performance Lab already has the core shape of a real training engine:

- a FastAPI backend
- a unified state model `S(t)`
- a stress dose layer `D(t)`
- a service loop for state updates
- a next-session prescription surface
- a frontend panel that already mirrors the control loop

That is the right foundation.

What it does **not** yet have is a fully closed product loop across onboarding,
planning, calibration, and production-hardening.

---

## Roadmap Themes

The project naturally breaks into five roadmap themes:

1. Core engine hardening
2. Onboarding and athlete setup
3. Planning and prescriber enrichment
4. Multi-modality expansion
5. Frontend and product integration

---

## 1. Core Engine Hardening

## 1.1 Add Alembic migrations
### Status ✅ COMPLETE

Two migrations are now in place:

- `a000_init` — creates all 9 foundational tables in FK-dependency order
  (users, athlete_profiles, exercises, mesocycle_blocks, planned_sessions,
  workout_logs, weak_points, athlete_states)
- `a001_benchmark_kpi_tables` — benchmark KPI tables, chains from a000

Run `alembic upgrade head` on a fresh DB to create all tables.

---

## 1.2 Expand automated testing
### Status ✅ SUBSTANTIALLY COMPLETE

Test files added in v0.3:

- `tests/test_dose_engine_v0.py` — 10 unit tests for `calculate_stress_dose`
- `tests/test_state_update_unit.py` — 10 unit tests for `update_athlete_state`
- `tests/test_orm_persistence.py` — 5 DB persistence tests
- `tests/test_integration_flow.py` — 4 end-to-end scenario tests
- `tests/conftest.py` — async DB fixture with `DROP SCHEMA CASCADE` isolation

### Remaining targets
- route tests for ingest and prescribe paths
- scenario tests for overload and deload behavior
- weak-point emergence and resolution scenarios

---

## 1.3 Improve configuration and local development ergonomics
### Status
Partly planned in README TODOs.

### Targets
- sane local `.env` examples
- `docker-compose` for Postgres-backed development
- tighter production CORS defaults
- clearer environment validation

---

## 1.4 Add model/version traceability
### Status ✅ INITIAL IMPLEMENTATION COMPLETE

`model_version = "v0.3"` is now a field on:

- `UnifiedStateVector` — every persisted state row carries which engine version produced it
- `WorkoutPrescription` — every prescription carries which engine version generated it

### Remaining targets
- migration strategy for historical states when formula changes
- replay strategy from event logs where needed

---

## 2. Onboarding and Athlete Setup

## 2.1 Add explicit onboarding endpoints
### Status ✅ COMPLETE

`POST /v1/onboard` is live. It:

- creates the AthleteProfile row
- optionally creates self-reported WeakPoint rows
- initializes baseline AthleteState S0 immediately
- accepts `experience_level`, `squat_1rm_kg`, `deadlift_1rm_kg`, `bench_1rm_kg`,
  `bodyweight_kg`, `run_5k_seconds` as optional inputs

The frontend onboarding form (`OnboardingForm.tsx`) calls this endpoint
immediately after registration.

---

## 2.2 Improve baseline state seeding
### Status ✅ COMPLETE

`initialize_athlete_state()` now accepts `experience_level` and `squat_1rm_kg`
keyword arguments and seeds from a 4-tier capacity table:

| Level        | c_met_aerobic | c_nm_force | c_struct | b_met_anaerobic |
|-------------|--------------|-----------|---------|----------------|
| beginner     | 180           | 500        | 60       | 8000            |
| intermediate | 300           | 1000       | 100      | 15000           |
| advanced     | 500           | 1800       | 160      | 25000           |
| elite        | 650           | 2500       | 220      | 35000           |

If `squat_1rm_kg` is provided, `c_nm_force = squat_1rm_kg * 10.0` overrides
the table value.

### Remaining targets
- better initialize skill state from profile data
- seed `habit_strength` from `experience_years`

---

## 2.3 Benchmark and assessment flow
### Status ✅ PARTIALLY COMPLETE

Planning/session flow now supports benchmark session slots and log payloads:

- `PlannedSession.is_benchmark` and `benchmark_key` are surfaced
- benchmark sessions can be scheduled via planning cadence
- `WorkoutLog` accepts `is_benchmark` + `benchmark_results`

### Targets
- feed benchmark results into state and weak-point updates
- richer benchmark type taxonomy beyond periodic retests

---

## 3. Planning and Prescriber Enrichment

## 3.1 Activate block and planned-session routes
### Status ✅ COMPLETE (MVP ROUTER LIVE)

`/v1/planning/*` is now implemented:

- create/list/update blocks
- list/update planned sessions
- retrieve today’s planned session slot with prescription context

### Why it matters
The model already distinguishes long-range intent from daily adaptation.
The product should expose that.

### Completed behavior
- block creation auto-generates session calendar rows
- workout logs can link to planned sessions
- `process_new_workout` auto-links same-day pending sessions when possible
- completed sessions are marked with `workout_log_id` + completion status

---

## 3.2 Enrich prescriber logic
### Status ✅ PARTIALLY COMPLETE

Implemented in v0.3:

- `active_weak_points` parameter — prescriber receives active unresolved WeakPoint
  tags from the DB; `constraints_applied` in the explanation is populated with
  `weak_point:{tag}` entries
- `block_context` parameter — when an active MesocycleBlock + today's PlannedSession
  exist, a +0.15 score bias is applied to candidates matching the planned session
  category; prescription is written back to `PlannedSession.prescribed_content`
- `exercises` field on WorkoutPrescription — populated by the prescriber's finalize step

### Remaining targets
- stronger constraint filtering by fatigue channel threshold values
- deeper exercise selection from DB exercise library (beyond current equipment mapping)
- benchmark-aware decision quality tuning
- clearer rationale generation using block goal context

---

## 3.3 Add exercise library seeding and management
### Status
Exercise model exists; seed/data workflow is not documented as complete.

### Targets
- initial seed set for major modalities
- benchmark exercise flags
- standardized movement-pattern taxonomy
- quality control for weak-point tags and coaching notes

---

## 3.4 Deload and rescheduling behavior
### Status
Supported by schema shape, not fully documented as complete behavior.

### Targets
- explicit deload session generation
- reschedule and skip handling
- persistence of session history vs actual completion

---

## 4. Multi-Modality Expansion

## 4.1 Move from tactical running roots to full modality coverage
### Status
Clearly stated in the README as a project direction.

### Why it matters
This is one of the project’s biggest strategic advantages: the engine is built
to model the athlete, not just one sport.

### Targets
- strengthen support for endurance
- strengthen support for strength and hypertrophy
- extend support for Olympic lifting
- extend support for calisthenics and mixed conditioning
- unify modality-specific logic under one stateful framework

---

## 4.2 Modality-aware APIs and policies
### Status
Explicitly planned in README.

### Targets
- versioned modality-aware endpoint behavior
- policy layers for different training domains
- shared core state with modality-specific interpretation where needed

---

## 4.3 Cross-talk refinement
### Status
Conceptually part of the architecture; not all internals are visible here.

### Targets
- improve interactions between metabolic, neuromuscular, and structural systems
- refine how one modality affects another
- better capture transfer and interference across training types

---

## 5. Data Assimilation and Model Calibration

## 5.1 Add data assimilation / EKF-style correction
### Status
Explicitly identified in the README as future work.

### Why it matters
The current engine can drift if it only projects forward from training logs.
Assimilation lets the model correct itself when real-world observations arrive.

### Targets
- incorporate benchmark/test observations back into state estimation
- correct model drift
- individualize parameter estimates over time
- support athlete-specific calibration rather than generic priors

### Long-term payoff
This is the step that moves the engine from a good adaptive heuristic system
closer to a true individualized digital twin.

---

## 6. Frontend and Product Integration

## 6.1 Mature the React frontend
### Status
Frontend v2 is explicitly planned; the current UI already demonstrates the core loop.

### Targets
- connect the frontend to the preferred API surface only
- move from demo panels to full athlete workflow
- support onboarding, daily session view, and history view
- expose rationale and state changes cleanly

---

## 6.2 Build the full athlete loop in the UI
### Targets
- onboarding flow
- current state view
- today’s session
- workout logging
- post-workout state delta summary
- block/calendar view
- benchmark and weak-point surfaces

---

## 6.3 Coach and debugging surfaces
### Targets
- inspect current `S(t)`
- inspect recent `D(t)` history
- compare planned vs completed sessions
- review active weak points and their sources
- view rationale behind the current prescription

---

## 7. Nice-to-Have but Not First

These are valuable, but should not outrank the core loop.

- polished deployment templates
- public demo environments
- multi-tenant admin tools
- analytics dashboards beyond debugging needs
- advanced collaboration features

The project wins first by making the engine correct, legible, and adaptive.

---

## Suggested Priority Order

If effort has to be sequenced tightly, the strongest order is:

### Phase 1 — Core Engine Hardening & First-Run Loop (✅ COMPLETE)

- [x] Alembic migrations: a000 (foundational) + a001 (benchmark KPI tables)
- [x] `POST /v1/onboard` endpoint (creates profile + weak points + seeds S0)
- [x] `GET /v1/next-session` auto-initializes baseline `AthleteState`
- [x] `POST /v1/log-workout` → `process_new_workout` → `UnifiedStateVector`
- [x] Profile-aware baseline seeding (4-tier experience_level + squat_1rm_kg)
- [x] model_version traceability on all response DTOs
- [x] Weak-point injection into prescriber (active unresolved tags from DB)
- [x] Block context injection into prescriber (active block + today's planned session)
- [x] Unit tests: dose engine (10), state update (10)
- [x] Integration tests: ORM persistence (5), end-to-end flow (4)
- [x] Import chain corrected: state_service imports from v0.3 engines

**Status**: The full quickstart flow from QUICKSTART_FLOW.md works end-to-end.

### Phase 2
- [x] block creation and calendar-generation routes (`/v1/planning/*`)
- [x] planned-session retrieval and completion linkage
- [x] equipment-aware prescription fallback/constraints tags
- [x] deload and benchmark planning flags in session flow
- exercise library seed data across all modalities

### Phase 3
- EKF / data assimilation
- multi-modality policy expansion
- frontend workflow completion

### Phase 4
- coach/debugging surfaces
- model version traceability improvements
- broader production hardening

---

## Short Version

The biggest roadmap items are:

1. harden the core engine
2. close the onboarding loop
3. expose planning and block logic
4. expand multi-modality support
5. add data assimilation / EKF correction
6. complete the frontend athlete experience

Those are the moves that turn Performance Lab from a promising engine into a
coherent product.
