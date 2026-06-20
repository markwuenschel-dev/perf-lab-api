---
status: accepted
date: 2026-06-20
---
# Use an explicit stress-dose layer `D(t)`

Workouts are translated into a `StressDose` before updating state
(`WorkoutLog -> StressDose -> AthleteState update`). The dose layer makes input
effects inspectable and enables non-mutating preview endpoints. The alternative —
ad hoc, workout-specific state mutation scattered through the codebase — was
rejected as un-inspectable.

**Guardrail:** normal workout-driven state transitions must pass through the dose layer.
