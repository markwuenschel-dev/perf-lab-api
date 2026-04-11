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
### Status
Clearly planned in the README, not yet configured.

### Why it matters
The model layer is already rich enough that schema drift will become painful
without migrations.

### Targets
- initialize Alembic
- create migration history for current ORM tables
- document migration workflow
- support reproducible local setup

---

## 1.2 Expand automated testing
### Status
Tooling is present; strategy and coverage are still growing.

### Why it matters
This is a stateful modeling system. Regressions will not always appear as route
errors; many will appear as subtle logic drift.

### Targets
- unit tests for dose engine
- unit tests for state update logic
- service tests for first-run and multi-session flows
- route tests for ingest and prescribe paths
- scenario tests for overload and deload behavior

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
### Status
Architecturally important, not visibly implemented.

### Why it matters
As dose formulas and update logic evolve, the project will need a clean way to
reason about which version produced which state.

### Targets
- version identifiers for dose/update logic
- migration strategy for historical states
- replay strategy from event logs where needed

---

## 2. Onboarding and Athlete Setup

## 2.1 Add explicit onboarding endpoints
### Status
Router is signposted in the main app, but not visibly active.

### Why it matters
The system already has the data model for baseline setup, but the first-run path
is not yet surfaced as a clean product flow.

### Targets
- create athlete profile
- capture equipment and schedule constraints
- capture baseline benchmarks
- optionally capture self-reported weak points
- initialize baseline `S0`
- return initial recommendation

---

## 2.2 Improve baseline state seeding
### Status
A safe default `S0` exists today.

### Why it matters
Generic defaults are useful for bootstrapping, but profile-informed seeding will
improve early recommendations.

### Targets
- derive baseline assumptions from profile fields
- use experience level and baseline performance to shape initial capacities
- better initialize skill state and habit strength

---

## 2.3 Benchmark and assessment flow
### Status
Supported by the data model conceptually, not yet fully surfaced as a product flow.

### Targets
- define benchmark session types
- store benchmark results in workout logs
- feed benchmark results into state and weak-point updates
- schedule benchmark retests intentionally

---

## 3. Planning and Prescriber Enrichment

## 3.1 Activate block and planned-session routes
### Status
Strong schema support exists; router surface is still planned.

### Why it matters
The model already distinguishes long-range intent from daily adaptation.
The product should expose that.

### Targets
- create blocks
- generate planned session calendars
- retrieve today’s session slot
- connect completed workout logs back to planned sessions

---

## 3.2 Enrich prescriber logic
### Status
Core concept exists and is already exposed via `next-session`; implementation details are only partly visible.

### Targets
- stronger constraint filtering by fatigue channel
- better use of block slot context
- weak-point-aware exercise biasing
- equipment-aware exercise selection
- clearer rationale generation
- benchmark-aware prescription decisions

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

- [x] Alembic migrations working
- [x] `POST /v1/onboard` endpoint (creates profile + weak points)
- [x] `GET /v1/next-session` auto-initializes baseline `AthleteState`
- [x] First real prescription returned from engine
- [x] Dev-friendly testing with `?user_id=1`

**Status**: The full quickstart flow from QUICKSTART_FLOW.md now works end-to-end.

### Phase 2
- block/planned-session APIs
- richer prescriber logic
- exercise library seeding
- deload and benchmark flows

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
