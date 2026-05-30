# Performance Lab Design Decisions

## Purpose

This document records the project's most important architectural decisions and why they exist.

It exists to prevent future cleanup from undoing the parts of the system that are conceptually correct.

This is not a changelog. It is a rationale log.

## Decision 1: Persist State History Instead of Only the Latest State

### Decision

Store athlete state as an append-only time series of `AthleteState` rows.

### Why

Performance Lab models an evolving internal system. The sequence of states matters, not only the latest snapshot.

Persisting state history gives:

- auditability
- trend visibility
- replay support
- easier debugging
- model-version migration options

### What this protects against

A tempting simplification is to keep one mutable current-state row per athlete. That would make it harder to inspect how the model changed over time or replay history after logic changes.

### Rule

Do not overwrite prior state rows as the default update pattern.

## Decision 2: Keep Workout Logs Separate From Athlete State

### Decision

Store observed workouts in `WorkoutLog` and modeled internal state in `AthleteState`.

### Why

These are different categories of truth:

- `WorkoutLog` = what happened
- `AthleteState` = what the engine believes after processing what happened

### What this protects against

Merging them makes old rows ambiguous after dose logic changes and makes replay/debugging much harder.

### Rule

Do not merge event history and modeled state for convenience.

## Decision 3: Use an Explicit Stress Dose Layer `D(t)`

### Decision

Translate workouts into `StressDose` before updating state.

```text
WorkoutLog -> StressDose -> AthleteState update
```

### Why

The dose layer makes input effects inspectable and enables non-mutating preview endpoints.

### What this protects against

Ad hoc workout-specific state mutation scattered through the codebase.

### Rule

Normal workout-driven state transitions must pass through the dose layer.

## Decision 4: Keep Preview and Mutation Separate at the API Layer

### Decision

Expose:

- `POST /v1/simulate-dose` for pure preview
- `POST /v1/log-workout` for real state transition

### Why

Clients need to ask two different questions:

1. What would this workout do?
2. This workout happened; update the athlete.

### Rule

Do not fuse preview and mutation paths.

## Decision 5: Model Fatigue as Multi-Dimensional

### Decision

Track multiple fatigue channels, including decomposed `fatigue_f` axes:

- cns
- muscular
- metabolic
- structural
- tendon
- grip

And legacy scalar mirrors:

- `f_met_systemic`
- `f_nm_peripheral`
- `f_nm_central`
- `f_struct_damage`

### Why

Different fatigue types constrain different training choices.

### What this protects against

Flattening fatigue into one readiness score too early, causing the prescriber to lose useful signal.

### Rule

A UI may show a summary readiness score, but the engine should preserve the channels.

## Decision 6: Separate Capacity, Fatigue, and Tissue Stress

### Decision

Store long-horizon capacities, short-horizon fatigues, and regional tissue stress separately.

### Why

A strong tired athlete is not the same as a weak fresh athlete. A globally fresh athlete with localized knee/lumbar tissue stress still needs constraints.

### Rule

Do not reduce the engine to either fitness-only or fatigue-only logic.

## Decision 7: Keep Legacy Scalar Mirrors Aligned With Engine Vectors

### Decision

Persist legacy scalar columns and decomposed `engine_state` JSONB together, with bridge helpers keeping them aligned.

### Why

This allows old clients to keep working while the engine evolves toward richer vector state.

### Tradeoff

It introduces duplication. The bridge layer must remain disciplined.

### Rule

When writing `AthleteState`, derive legacy columns from the unified vector rather than manually letting them drift.

## Decision 8: Store Weak Points as Probabilistic Signals

### Decision

Represent weak points as rows with tag, source, confidence, note, and resolved status.

### Why

Weakness can come from self-report, benchmark, inference, or performance data. Those sources have different confidence levels and should not be collapsed into one permanent trait.

### Rule

Do not treat weak points as one static athlete attribute.

## Decision 9: Weak Points Bias the Prescriber, They Do Not Hijack It

### Decision

Active weak-point tags are passed into prescription logic as bias signals and explanation constraints.

### Why

A limitation is useful information, not absolute command authority.

### Rule

Weak points should influence exercise choice, assistance work, and emphasis after safety, goal, and block context are respected.

## Decision 10: Keep Planning and Adaptation Separate

### Decision

Use `MesocycleBlock` and `PlannedSession` for strategic structure, while the prescriber uses current state to fill daily content.

### Why

A block provides direction. The athlete state controls dosage and constraints today.

### What this protects against

Two bad extremes:

1. rigid plans that ignore readiness
2. reactive day-by-day generators that lose long-term coherence

### Rule

Do not let a block eliminate adaptive state-based prescription. Do not let daily adaptation erase block identity.

## Decision 11: Populate Planned Session Content Lazily

### Decision

A `PlannedSession` stores the slot first. `prescribed_content` is written when today's session is opened or a matching next-session prescription is generated.

### Why

The exact content should use fresh `S(t)`, weak points, KPIs, and recent workout context.

### Rule

Do not precompute exact session content too early unless the product explicitly wants a static plan.

## Decision 12: Link Completed Workouts to Planned Sessions

### Decision

A completed workout links to a planned session through `planned_session_id` / `workout_log_id`.

### Why

Plans and completions are different. Linking preserves both.

### Rule

Do not replace planned-session rows with observed workout data.

## Decision 13: Use Benchmarks as a Separate Measurement Layer

### Decision

Represent benchmark protocols and benchmark observations separately from workouts.

### Why

Benchmarks are measurements. They may happen inside planned sessions, but their role is to calibrate or validate the model.

### Rule

Use `BenchmarkObservation` and `ObservationMapping` when a measurement should update state or weak-point signals.

## Decision 14: Store Derived KPIs as Snapshots

### Decision

Compute derived metrics from observations and store `DerivedMetricSnapshot` rows.

### Why

Derived KPI values can change as observations or formulas change. Snapshotting provides an audit trail and dashboard history.

### Rule

Do not hide all KPI computation as transient UI-only logic.

## Decision 15: Use Observation Mappings Before Full EKF Complexity

### Decision

Current benchmark assimilation uses weighted residual-style mapping rules rather than a full EKF.

### Why

This is enough to close the benchmark-to-state loop while keeping the system legible and easy to debug.

### Tradeoff

It is less theoretically complete than a full state estimator.

### Rule

Keep mappings explicit and auditable until the model is mature enough to justify heavier assimilation machinery.

## Decision 16: Use the Exercise Library as a Metadata Layer

### Decision

Store movement metadata, equipment, phi vectors, tissue/fatigue weights, and weak-point tags in `Exercise` rows.

### Why

The prescriber and dose engine need structured movement information. Hard-coded choices do not scale across modalities.

### Rule

Prefer data-driven exercise selection and phi resolution over sprawling if/else branches.

## Decision 17: Keep Auth Outside `/v1`

### Decision

Auth routes live at `/auth/*`, while modern domain routes live under `/v1`.

### Why

The token endpoint uses OAuth2 password-form conventions and is consumed differently from the versioned training API.

### Rule

Do not accidentally document auth as `/v1/auth/*` unless the router is actually moved.

## Decision 18: Use Alembic as the Only Schema Manager

### Decision

The app startup checks database connectivity and Alembic head, but it does not call `create_all`.

### Why

Auto-creating tables at startup hides migration problems and creates schema drift.

### Rule

Use `alembic upgrade head` before running against a real database.

## Decision 19: Keep Deprecated Transition Modules Thin

### Decision

`app.logic.dose_engine` and `app.logic.state_update` are compatibility/deprecation modules. Current code should use:

- `app.logic.dose_engine_v0.calculate_stress_dose`
- `app.logic.state_update_v0.update_athlete_state`
- `app.services.state_service.process_new_workout`

### Why

This preserves old imports while making the preferred implementation explicit.

### Rule

New code should not build on deprecated modules.

## Decision 20: Frontend Types Are Manual Mirrors

### Decision

`src/types.ts` manually mirrors backend Pydantic schemas. It is not generated.

### Why

The project is small enough that manual sync is acceptable, but the cost is that backend schema changes must be reflected deliberately.

### Rule

Whenever backend schemas change, update `src/types.ts`, `trainingGoals.ts`, the API client, and relevant form/rendering components.

## Decision 21: Keep the Frontend Control Loop Close to the Backend Domain

### Decision

The UI names and flow mirror backend concepts:

```text
simulate-dose -> log-workout -> next-session
planning block -> planned session -> today's session
```

### Why

This preserves conceptual legibility and avoids creating a separate UI-only mental model.

### Rule

Do not hide the control loop behind vague UI abstractions too early.

## Decision 22: Favor Legibility Over Premature Cleverness

### Decision

Prefer explicit state, dose, plan, benchmark, weak-point, and explanation structures.

### Why

A system that cannot be explained cannot be debugged or trusted.

### Rule

Accept some verbosity when it makes the control loop easier to inspect and defend.

## Short Version

The key design bets are:

1. persist state history
2. separate logs from state
3. use an explicit dose layer
4. keep preview and mutation separate
5. preserve multi-channel fatigue and tissue state
6. keep planning and adaptation distinct
7. use weak points as probabilistic bias signals
8. use benchmarks as state-calibration observations
9. store derived KPIs as snapshots
10. use exercise metadata instead of hard-coded movements
11. keep frontend DTOs synchronized with backend schemas
