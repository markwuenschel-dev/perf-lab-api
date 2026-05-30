---
task_id: TASK-003
from: orchestrator
to: coder
timestamp: 2026-05-29 00:11
turn: 1
cycle: 1
status: done
needs: coder
assigned_to: coder
---

## Task Title
Migrate `app/api/v1/ingest.py` off the deprecated `app.logic.dose_engine` import to `app.logic.dose_engine_v0`, and add a ruff lint guard to catch any future callers at CI time.

## Goal
`app/api/v1/ingest.py` must import `calculate_stress_dose` from `app.logic.dose_engine_v0` (the current production engine) rather than from `app.logic.dose_engine` (deprecated, emits `DeprecationWarning` at module load). A ruff lint rule (`TID252`) must be configured in `pyproject.toml` to make `app.logic.dose_engine` a banned API, so any future accidental import fails CI.

## Context

**Deprecated import to remove** (`app/api/v1/ingest.py`, line 9):
```python
from app.logic.dose_engine import calculate_stress_dose
```

**Replacement import:**
```python
from app.logic.dose_engine_v0 import calculate_stress_dose
```

**Why this matters:**
- `app/logic/dose_engine.py` emits a `DeprecationWarning` at module level (lines 22–27) every time `ingest.py` is imported, polluting logs in production.
- `dose_engine.calculate_stress_dose` is actually an alias for the old dict-based `calculate_stress_doses` (line 65), which accepts a plain `dict` — incompatible with the `WorkoutLog` objects that the `/simulate-dose` route passes in. The route is silently calling the wrong engine.
- `dose_engine_v0.calculate_stress_dose(log: WorkoutLog) -> StressDose` is the correct typed interface already used by the rest of the system.

**Interface verification:**
- `app/logic/dose_engine_v0.py`, line 159: `def calculate_stress_dose(log: WorkoutLog) -> StressDose:` — same name, correct typed signature. Drop-in replacement.

**Lint guard approach:**
- `pyproject.toml` already uses ruff `>=0.5.0` with `select = ["E", "F", "I", "UP", "B", "C4"]`
- Add `"TID"` to the `select` list to enable flake8-tidy-imports rules
- Add a `[tool.ruff.lint.flake8-tidy-imports]` section with `banned-api` entry banning `app.logic.dose_engine`
- Ruff rule `TID251` bans specific names; `TID252` bans entire modules. Use `TID252` to ban `app.logic.dose_engine`.

**Files to edit (only files under `app/`, `tests/`, or config files at repo root):**
1. `app/api/v1/ingest.py` — swap the import on line 9
2. `pyproject.toml` — extend ruff lint config with `TID` rule and `banned-api` entry

**No test file changes required** for this task (the import fix is verified by the lint guard itself), but a smoke test confirming the `/simulate-dose` route still accepts a `WorkoutLog` and returns a `StressDose` is welcome if already present in `tests/`.

## Acceptance Criteria
- [x] AC-1: `app/api/v1/ingest.py` does NOT contain the string `from app.logic.dose_engine import` (grep: `grep -n "from app.logic.dose_engine import" app/api/v1/ingest.py` returns no lines)
- [x] AC-2: `app/api/v1/ingest.py` contains `from app.logic.dose_engine_v0 import calculate_stress_dose` (grep-verifiable)
- [x] AC-3: `pyproject.toml` contains `"TID"` in the ruff lint `select` list (grep-verifiable)
- [x] AC-4: `pyproject.toml` contains a `[tool.ruff.lint.flake8-tidy-imports]` section (grep-verifiable)
- [x] AC-5: `pyproject.toml` contains `"app.logic.dose_engine"` under `banned-api` (grep-verifiable)

## Attachments
- findings: none
- critique: agent_collab/outbox/critic/TASK-003_critic_to_orchestrator_2026-05-29.md

## Dependencies
TASK-001 (done), TASK-002 (done)

## History
| Timestamp        | Action                              | By           |
|------------------|-------------------------------------|--------------|
| 2026-05-29 00:11 | Created, status pending             | orchestrator |
| 2026-05-29 00:11 | Claimed, assigned to coder          | orchestrator |
| 2026-05-29 00:20 | Critic verdict: APPROVED, all 5 ACs passed | critic  |
| 2026-05-29 00:20 | Closed, moved to done/              | orchestrator |
