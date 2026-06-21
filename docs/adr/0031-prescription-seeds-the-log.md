---
status: accepted
date: 2026-06-21
---
# Prescription seeds the workout log

The engine prescribes specific exercises (sets/reps/loads, in
`PlannedSession.prescribed_content`), but `process_new_workout` only computed an
exercise-aware dose when the *client* re-sent `log.exercises` — which it almost never
does — so logged workouts fell back to coarse modality defaults and the per-capacity
adaptation signal was thin. We close the loop: when a `WorkoutLog` fulfills a planned
session (`planned_session_id`), the engine seeds `log.exercises` from that session's
`prescribed_content`; the athlete confirms or edits, and the delta (did 4×5 not 5×5,
last set RPE 9) is the signal. Exercise-level dose becomes the **default** for planned
work at near-zero added friction. Ad-hoc / unplanned workouts keep the cheap three-field
(`modality`, `duration`, `session_rpe`) path and its modality-default dose. We rejected
requiring full per-exercise entry everywhere (a logging tax that kills adherence) and
leaving the loop open (the rich dose math stays dormant).

Relates to [ADR-0003](0003-explicit-dose-layer.md) (explicit dose layer),
[ADR-0011](0011-lazy-planned-session-content.md) (lazy prescribed content),
[ADR-0016](0016-exercise-metadata-layer.md) (exercise metadata).

**Guardrail:** a planned session's prescribed exercises are the default source for its
fulfilling log's dose. Never require manual per-exercise entry to get exercise-level
adaptation on planned work.
