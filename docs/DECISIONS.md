# Performance Lab Design Decisions

## Purpose

This document records the project’s most important architectural decisions and
why they were made.

It exists for one reason:

> to stop future “cleanup” from undoing the parts of the system that are
> conceptually correct.

This is not a changelog.
It is a rationale log.

---

## How to Use This Document

Each section records:

- the decision
- why it exists
- what it protects against
- what tradeoff it accepts

These are not abstract philosophy notes.
They are guardrails for future development.

---

## Decision 1: Persist State History Instead of Recomputing Only the Latest State

### Decision
Store athlete state as an append-only time series of `AthleteState` rows instead
of keeping only one mutable “current state” record.

### Why
Performance Lab is modeling an evolving internal system.
That makes the sequence of states meaningful, not just the latest snapshot.

Persisting state history gives the project:
- auditability
- trend visibility
- replay support
- easier debugging
- future model migration options

### What this protects against
A common simplification is to store only one current-state row per athlete.
That feels cleaner at first and is wrong for this system.

It would make it harder to:
- inspect how the model changed over time
- compare prior and current estimates
- understand the effect of a workout sequence
- replay history after logic changes

### Tradeoff
It increases storage and adds a little more query discipline.
That tradeoff is worth it.

### Rule
Do not overwrite prior state rows as a default implementation pattern.

---

## Decision 2: Keep Workout Logs Separate from Athlete State

### Decision
Store raw workout events in `WorkoutLog` and modeled internal state in
`AthleteState`.

### Why
These are different categories of truth.

- `WorkoutLog` = what happened
- `AthleteState` = what the engine believes after processing what happened

If they are merged, the system loses the distinction between observation and
interpretation.

### What this protects against
If a future refactor collapses logs and state into one table, it becomes harder to:
- replay history with updated logic
- debug incorrect model transitions
- tell whether a value came from user input or model inference

### Tradeoff
There is some duplication in lifecycle complexity.
That is acceptable because the conceptual separation is essential.

### Rule
Do not merge event history and modeled state for convenience.

---

## Decision 3: Use an Explicit Stress Dose Layer `D(t)`

### Decision
Translate a workout into a `StressDose` before updating the athlete state.

### Why
Workouts should not directly mutate internal state without an intermediate model
layer.

The dose layer creates a clean mapping:

```text
WorkoutLog → StressDose → AthleteState update
```

This supports:
- better reasoning about input effects
- non-mutating preview endpoints
- modality expansion under a shared framework
- cleaner debugging of the control loop

### What this protects against
A tempting shortcut is to let each workout directly change state using ad hoc
logic embedded everywhere.
That makes the system harder to reason about and much harder to generalize.

### Tradeoff
It introduces an extra abstraction layer.
That is a feature, not overhead, in a modeling system.

### Rule
Do not bypass the dose layer for normal state transitions.

---

## Decision 4: Model Fatigue as Multi-Dimensional, Not a Single Readiness Number

### Decision
Track multiple fatigue channels instead of flattening everything into one score.

Current fatigue channels include:
- `f_met_systemic`
- `f_nm_peripheral`
- `f_nm_central`
- `f_struct_damage`

### Why
Different forms of fatigue constrain different training choices.

Examples:
- high structural damage should affect impact and loading decisions
- high central fatigue should affect neural and technical work
- high metabolic fatigue should affect dense conditioning or threshold-style work

A single fatigue number hides those distinctions too early.

### What this protects against
If everything is flattened into one readiness score at the model level, the
prescriber loses the ability to make intelligent constrained choices.

### Tradeoff
The system becomes slightly more complex to inspect.
That complexity is justified because it preserves useful signal.

### Rule
Do not collapse the underlying fatigue model into one scalar internally.
A UI may show a summary score, but the engine should keep the channels.

---

## Decision 5: Separate Capacity From Fatigue

### Decision
Store long-horizon capacities separately from short-horizon fatigue.

### Why
Performance today is shaped by both:
- what the athlete is capable of in general
- what cost they are carrying right now

Those are not the same thing.

A strong but tired athlete should not be treated like a weak fresh athlete.
Likewise, a fresh but underdeveloped athlete should not be treated like a fully
adapted one.

### What this protects against
Programs that only read recent fatigue tend to lose progression logic.
Programs that only read capacity tend to ignore recovery reality.

### Tradeoff
It requires maintaining more state variables.
That is central to the project’s whole value proposition.

### Rule
Do not reduce the engine to either “fitness only” or “fatigue only.”
It must keep both.

---

## Decision 6: Store Weak Points as Probabilistic Signals, Not Permanent Traits

### Decision
Represent weak points as rows with:
- tag
- source
- confidence
- note
- active/resolved status

Instead of storing a single fixed weakness label per athlete.

### Why
Weakness is often uncertain, context-dependent, and multi-sourced.

A self-reported weakness is not the same as one revealed by a benchmark or
inferred from repeated training behavior.

The current structure allows the system to accumulate and weigh evidence over time.

### What this protects against
A simplistic “athlete has weakness X” flag becomes stale fast and erases source quality.
It also makes it harder to resolve or re-evaluate weak points later.

### Tradeoff
It adds aggregation complexity for the prescriber.
That is worth it because the data model remains truthful.

### Rule
Do not treat weak points as one permanent static attribute.

---

## Decision 7: Keep Planning and Adaptation Separate

### Decision
Separate long-range plan structures from day-level adaptive decisions.

This is reflected in:
- `MesocycleBlock`
- `PlannedSession`
- the prescriber’s dynamic filling of `prescribed_content`

### Why
A block should provide direction.
The current state should determine today’s exact dosage and implementation.

This preserves both:
- program coherence
- adaptive responsiveness

### What this protects against
Two opposite failure modes:

1. a rigid static plan that ignores readiness
2. a reactive day-by-day generator that loses long-term direction

### Tradeoff
The system has to maintain both a plan layer and a control layer.
That is the right architecture for adaptive training.

### Rule
Do not let the existence of a block eliminate state-based adaptation.
Do not let daily adaptation erase block identity.

---

## Decision 8: Use the Exercise Library as a Metadata Layer, Not Hard-Coded Choices

### Decision
Store exercise characteristics in structured seed data rather than hard-coding
selection logic everywhere.

The exercise model includes:
- modality
- movement pattern
- equipment requirements
- load type
- skill demand
- impact level
- weak-point tags
- coaching notes

### Why
The prescriber needs a flexible bridge from abstract training intent to concrete
movement selection.

A metadata-driven exercise library scales better than a large pile of brittle
if/else branches.

### What this protects against
Hard-coded exercise selection becomes difficult to maintain as modalities,
equipment options, and weak-point logic expand.

### Tradeoff
The project must maintain high-quality seed data.
That is an acceptable price for extensibility.

### Rule
Prefer data-driven exercise selection over sprawling hard-coded branching.

---

## Decision 9: Keep Preview and Mutation Separate at the API Layer

### Decision
Support both:
- non-mutating dose preview
- mutating workout logging

Through separate endpoints.

### Why
The client needs to ask two different questions:

1. what would this workout do?
2. this workout happened — update the athlete

Those should not be the same API call.

### What this protects against
If preview and mutation are fused, the system becomes dangerous to integrate.
Clients will accidentally advance the athlete model while experimenting.

### Tradeoff
It adds one more endpoint.
That is trivial compared to the clarity it provides.

### Rule
Keep `simulate-dose` non-mutating and `log-workout` mutating.

---

## Decision 10: Treat Human Factors as First-Class Inputs

### Decision
Include human-context fields such as sleep quality and life-stress inverse in
workout input and state evolution.

### Why
Training response is not determined by external load alone.
Two identical workouts can have different internal cost depending on recovery
context.

### What this protects against
A purely mechanical load model will look clean and behave unrealistically.

### Tradeoff
Self-reported fields are noisy.
That is fine as long as the system treats them as informative context, not
perfect measurement.

### Rule
Do not strip human factors out of the model just to make inputs look cleaner.

---

## Decision 11: Bias the Prescriber With Weak Points, but Do Not Let Them Dominate

### Decision
Weak points should influence prescription, but not automatically override the
main goal or current safety constraints.

### Why
A detected limitation is useful information, not absolute command authority.

The right pattern is usually:
- preserve session identity
- bias exercise choice or assistance work
- avoid reckless choices when fatigue says no

### What this protects against
A prescriber that overreacts to every weak point becomes incoherent and loses
block structure.

### Tradeoff
It requires more nuanced decision logic.
That nuance is necessary.

### Rule
Weak points should bias selection, not hijack programming.

---

## Decision 12: Favor Legibility Over Premature Cleverness

### Decision
Keep the architecture explainable:
- explicit state model
- explicit dose layer
- explicit planning objects
- explicit weak-point rows
- explicit rationale in prescriptions

### Why
A system that cannot be explained cannot be debugged or trusted for long.

### What this protects against
Premature compression into opaque abstractions that look elegant but make
maintenance and reasoning worse.

### Tradeoff
Some parts of the system will look more verbose than a minimalist prototype.
That is a good trade in this domain.

### Rule
Prefer structures that make the control loop easy to inspect and defend.

---

## Short Version

The key design bets in Performance Lab are:

1. persist state history
2. separate logs from state
3. use an explicit dose layer
4. keep fatigue multi-dimensional
5. separate capacity from fatigue
6. model weak points as probabilistic signals
7. keep planning and adaptation separate
8. use metadata-driven exercise selection
9. separate preview from mutation in the API
10. keep human factors in the loop

If future development preserves those, the project will stay pointed in the
right direction.
