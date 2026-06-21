---
status: accepted
date: 2026-06-20
---
# Keep workout logs separate from athlete state

Observed workouts live in `WorkoutLog`; modeled internal state lives in
`AthleteState`. These are different categories of truth — what *happened* versus what
the engine *believes* after processing what happened. Merging them makes old rows
ambiguous after dose logic changes and makes replay and debugging much harder.

**Guardrail:** do not merge event history and modeled state for convenience.
