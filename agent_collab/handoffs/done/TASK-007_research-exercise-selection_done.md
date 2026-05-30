---
task_id: TASK-007
from: orchestrator
to: researcher
timestamp: 2026-05-29 00:50
turn: 1
cycle: 1
status: done
needs: researcher
assigned_to: none
---

## Task Title
Research the exercise selection path from `recommend_next_session` to identify all call sites that must change to enable DB-driven exercise selection from the `Exercise` ORM table.

## Goal
The prescriber currently selects exercises from a hardcoded in-memory dict (`_EQUIPMENT_EXERCISE_MAP`) and hardcoded `SessionCandidate` `.focus` strings. The `Exercise` ORM table (`app/models/exercise.py`) already exists and has the right schema. This research task traces the full exercise selection path and produces a precise call-site map that a Coder can act on without re-reading every file.

Do NOT make any code changes. Produce only a findings file.

---

## Scope

Read these files in order:

1. `app/logic/prescriber.py` — entry point (`recommend_next_session`) and exercise selection logic
2. `app/models/exercise.py` — the Exercise ORM table (already exists)
3. `app/engine/config.py` — modality weights and any exercise-related config
4. `app/schemas/prescription.py` — `ExercisePrescription` and `WorkoutPrescription` output schemas
5. `app/logic/prescription_finalize.py` — called by `_finalize()` at `prescriber.py:718`

If additional files are imported by any of the above and are directly relevant to exercise selection, read them too and list them in Files Examined.

---

## Questions to Answer

**Q1 — Entry point**
Where does `recommend_next_session` live, and what is its full signature? (`prescriber.py:761`)

**Q2 — Exercise selection call sites**
List every location (file:line) where concrete exercise names are hardcoded or selected. Include:
- The `_EQUIPMENT_EXERCISE_MAP` dict and `_exercise_list_for_equipment` function
- Any `SessionCandidate.focus` string that embeds exercise names (e.g. "Back Squat 5×3 @ RPE 8")
- Any other location where exercise names are produced

**Q3 — Output schema**
What fields does `ExercisePrescription` expose? What fields does `WorkoutPrescription.exercises` (list of `ExercisePrescription`) expose? Where are they defined?

**Q4 — Exercise ORM table schema**
List the columns of `app/models/exercise.py:Exercise` that are relevant to exercise selection filtering:
- equipment matching column(s)
- modality / movement pattern columns
- weak point targeting column(s)
- is_benchmark column

**Q5 — Gap analysis**
For each call site from Q2, state what DB query / filter would replace it, referencing the Exercise ORM columns from Q4. Keep this factual — do not design the implementation, just name the columns that map to the selection criteria currently hardcoded.

---

## Acceptance Criteria

- [x] AC-1: Findings file exists at `agent_collab/outbox/researcher/TASK-007_researcher_to_orchestrator_2026-05-29.md`
- [x] AC-2: Entry point `recommend_next_session` documented with exact file:line and full signature
- [x] AC-3: All hardcoded exercise-name call sites listed with file:line evidence
- [x] AC-4: `ExercisePrescription` schema fields documented with file:line
- [x] AC-5: `Exercise` ORM table columns relevant to selection documented with file:line
- [x] AC-6: Gap analysis table maps each call site to the ORM column(s) that would replace the hardcoded lookup

---

## Output

Write findings to:
`agent_collab/outbox/researcher/TASK-007_researcher_to_orchestrator_2026-05-29.md`

Use the standard researcher findings format (see `agent_collab/context/researcher_onboarding.md`).

---

## Files NOT to Touch
- `app/` — read only, no edits
- `tests/` — no changes
- `docs/` — no changes
- Any handoff, inbox, or state file except your outbox

---

## Dependencies
TASK-001 (done), TASK-002 (done), TASK-003 (done), TASK-004 (done), TASK-005 (done), TASK-006 (done)

## History
| Timestamp        | Action                                          | By           |
|------------------|-------------------------------------------------|--------------|
| 2026-05-29 00:50 | Created and claimed (skip pending), cycle 1     | orchestrator |
| 2026-05-29 00:50 | Assigned to researcher, cycle 1                 | orchestrator |
| 2026-05-29 01:00 | Researcher findings complete. Accepted.         | orchestrator |
