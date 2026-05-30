---
task_id: TASK-003
from: coder
to: orchestrator
timestamp: 2026-05-29
turn: 1
status: done
---

## TASK-003 Implementation Summary

### Changes Made

**`app/api/v1/ingest.py` (line 9)**
- Replaced: `from app.logic.dose_engine import calculate_stress_dose`
- With: `from app.logic.dose_engine_v0 import calculate_stress_dose`
- No other changes made to this file.

**`pyproject.toml` (`[tool.ruff.lint]` section)**
- Added `"TID"` to the `select` list: `select = ["E", "F", "I", "UP", "B", "C4", "TID"]`
- Added new sections:
  ```toml
  [tool.ruff.lint.flake8-tidy-imports]
  banned-module-level-imports = []

  [tool.ruff.lint.flake8-tidy-imports.banned-api]
  "app.logic.dose_engine".msg = "Use app.logic.dose_engine_v0 instead — dose_engine is deprecated"
  ```

### Acceptance Criteria Verification

| AC   | Check                                                      | Result |
|------|------------------------------------------------------------|--------|
| AC-1 | `ingest.py` does NOT contain `from app.logic.dose_engine import` | PASS (grep returns nothing) |
| AC-2 | `ingest.py` contains `from app.logic.dose_engine_v0 import calculate_stress_dose` | PASS (line 9) |
| AC-3 | `pyproject.toml` contains `"TID"` in ruff lint select list | PASS |
| AC-4 | `pyproject.toml` contains `[tool.ruff.lint.flake8-tidy-imports]` | PASS |
| AC-5 | `pyproject.toml` contains `"app.logic.dose_engine"` under `banned-api` | PASS |

### Verification Runs

**pytest --collect-only -k "ingest or dose"**
- 21 tests collected across:
  - `tests/test_dose_engine_v0.py` (10 tests)
  - `tests/test_ingest_routes.py` (8 tests)
  - `tests/test_orm_persistence.py` (1 test)
  - `tests/test_state_update_v2.py` (2 tests)
- 6 pre-existing collection errors in unrelated test files (constraint_engine missing export, missing numpy in system Python) — NOT caused by TASK-003 changes.

**ruff check app/api/v1/ingest.py --select TID**
- Output: `All checks passed!`
- No TID errors on the fixed file (confirmed the new import from `dose_engine_v0` is not banned).

**python -W error::DeprecationWarning -c "import app.api.v1.ingest"**
- Output: (empty — no warnings, clean exit)
- Confirmed: no DeprecationWarning emitted at import time.

**ruff check app/api/v1/ingest.py** (full, without TID filter)
- Returns 4 pre-existing errors (I001 import sort, B008 x2 Depends in defaults, B904 raise-from) — all existed before this task and are out of scope.

### Notes
- The 6 pytest collection errors are pre-existing failures unrelated to this task: `test_dose_engine.py` fails due to missing `numpy` in the system Python (the venv has it but is Windows-based), and the constraint_engine/prescriber/scenarios tests fail due to a missing `encode_session_candidate` export — a separate issue.
- The `ruff.exe` from the project `.venv/Scripts/` was used (ruff 0.14.5, Python 3.12.9).
