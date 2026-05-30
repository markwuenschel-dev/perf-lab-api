---
task_id: TASK-003
from: orchestrator
to: coder
timestamp: 2026-05-29 00:11
turn: 1
status: claimed
---

## Assignment: Migrate deprecated dose_engine import in ingest.py

You have been assigned **TASK-003** (cycle 1 of max 3).

**Claimed handoff:** `agent_collab/handoffs/claimed/TASK-003_migrate-dose-engine-import_claimed.md`

Read the full handoff for exact details. Key facts are summarised below.

---

### Problem
`app/api/v1/ingest.py` imports `calculate_stress_dose` from `app.logic.dose_engine`, which is the **deprecated** module. That module:
- Emits a `DeprecationWarning` at import time on every server start.
- Exposes `calculate_stress_dose` as an alias for the old **dict-based** `calculate_stress_doses`, which is incompatible with the `WorkoutLog` objects the route passes in — the route is silently calling the wrong engine.

### Files you must edit
1. `app/api/v1/ingest.py` — swap one import line
2. `pyproject.toml` — extend ruff lint config to ban `app.logic.dose_engine`

### Exact change for `app/api/v1/ingest.py`
Replace line 9:
```python
# BEFORE
from app.logic.dose_engine import calculate_stress_dose

# AFTER
from app.logic.dose_engine_v0 import calculate_stress_dose
```
No other changes to `ingest.py` are needed — the function name is the same.

### Exact change for `pyproject.toml`
In `[tool.ruff.lint]`, add `"TID"` to the `select` list:
```toml
select = ["E", "F", "I", "UP", "B", "C4", "TID"]
```

Add a new section after `[tool.ruff.lint]`:
```toml
[tool.ruff.lint.flake8-tidy-imports]
banned-api = { "app.logic.dose_engine" = "Deprecated engine — import from app.logic.dose_engine_v0 instead." }
```

### Acceptance criteria (binary, grep-verifiable)
- AC-1: `app/api/v1/ingest.py` does NOT contain `from app.logic.dose_engine import`
- AC-2: `app/api/v1/ingest.py` contains `from app.logic.dose_engine_v0 import calculate_stress_dose`
- AC-3: `pyproject.toml` contains `"TID"` in the ruff lint select list
- AC-4: `pyproject.toml` contains `[tool.ruff.lint.flake8-tidy-imports]`
- AC-5: `pyproject.toml` contains `"app.logic.dose_engine"` under `banned-api`

### Rules reminder
- You may only edit files under `app/`, `tests/`, `docs/`, and root-level config files (`pyproject.toml`).
- Do NOT modify anything under `agent_collab/`.
- When done, write your output to `agent_collab/outbox/coder/` and notify the orchestrator.
