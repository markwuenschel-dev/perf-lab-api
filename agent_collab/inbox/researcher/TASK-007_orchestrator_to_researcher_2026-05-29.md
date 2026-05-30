---
task_id: TASK-007
from: orchestrator
to: researcher
timestamp: 2026-05-29 00:50
turn: 1
cycle: 1
---

## Assignment: TASK-007 — research-exercise-selection

You have been assigned TASK-007. Read the full handoff at:

`agent_collab/handoffs/claimed/TASK-007_research-exercise-selection_claimed.md`

### Summary

Trace the prescriber exercise selection path from `recommend_next_session` through to what data is returned. Produce a findings file mapping every call site that needs to change to enable DB-driven exercise selection from the `Exercise` ORM table (`app/models/exercise.py`).

### Files to Read (in order)

1. `app/logic/prescriber.py` — entry point and all exercise selection logic
2. `app/models/exercise.py` — the Exercise ORM table schema
3. `app/engine/config.py` — modality weights and config
4. `app/schemas/prescription.py` — output schemas (`ExercisePrescription`, `WorkoutPrescription`)
5. `app/logic/prescription_finalize.py` — called by prescriber `_finalize()` at line 718

### Your Output

Write findings to:
`agent_collab/outbox/researcher/TASK-007_researcher_to_orchestrator_2026-05-29.md`

Cover all 6 acceptance criteria from the handoff. Every factual claim must have a `file:line` citation.

### Do NOT

- Edit any file under `app/`, `tests/`, or `docs/`
- Make implementation decisions — surface facts only

This is a cycle-1 task. Complete in one pass.
