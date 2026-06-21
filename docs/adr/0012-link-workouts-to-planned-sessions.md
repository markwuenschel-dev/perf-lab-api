---
status: accepted
date: 2026-06-20
---
# Link completed workouts to planned sessions

A completed workout links to its planned session through `planned_session_id` /
`workout_log_id` rather than replacing the planned row. Plans and completions are
different facts; linking preserves both the intended slot and what actually happened.

**Guardrail:** do not replace planned-session rows with observed workout data.
