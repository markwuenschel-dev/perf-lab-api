---
task_id: TASK-002
from: orchestrator
to: coder
timestamp: 2026-05-29 00:05
turn: 1
status: claimed
---

## Assignment: Persist baseline fields in onboard endpoint

You have been assigned **TASK-002** (cycle 1 of max 3).

**Claimed handoff:** `agent_collab/handoffs/claimed/TASK-002_persist-baseline-fields_claimed.md`

Read the full handoff for exact details. Key facts are summarised below.

---

### Problem
`POST /v1/onboard` accepts five baseline fields from the client but silently drops four of them — only `squat_1rm_kg` is forwarded to `initialize_athlete_state`; none of the five are assigned to `AthleteProfile` columns.

### Files you must edit
1. `app/api/v1/onboard.py`
2. `app/services/state_service.py`
3. A test file under `tests/` (extend `test_integration_flow.py` or create `tests/test_onboard_baseline_fields.py`)

### Schema → ORM mapping
| Request field      | `AthleteProfile` column |
|--------------------|------------------------|
| `squat_1rm_kg`     | `squat_1rm`            |
| `deadlift_1rm_kg`  | `deadlift_1rm`         |
| `bench_1rm_kg`     | `bench_1rm`            |
| `bodyweight_kg`    | `bodyweight_kg`        |
| `run_5k_seconds`   | `run_5k_seconds`       |

### What to do in each file

**`app/api/v1/onboard.py`** — inside the profile-upsert block, after the existing five assignments, add:
```python
profile.squat_1rm     = request.squat_1rm_kg
profile.deadlift_1rm  = request.deadlift_1rm_kg
profile.bench_1rm     = request.bench_1rm_kg
profile.bodyweight_kg = request.bodyweight_kg
profile.run_5k_seconds = request.run_5k_seconds
```
Also extend the `initialize_athlete_state` call to pass all five keyword arguments.

**`app/services/state_service.py`** — extend the `initialize_athlete_state` signature with four new keyword params (`deadlift_1rm_kg`, `bench_1rm_kg`, `bodyweight_kg`, `run_5k_seconds`) and forward them to `_build_baseline_vector`.

**Test** — POST /v1/onboard with all five baseline fields set and assert the returned profile (or a DB read) shows non-null values for every mapped column.

### Acceptance criteria (binary, grep-verifiable)
- AC-1: `app/api/v1/onboard.py` contains `profile.deadlift_1rm = request.deadlift_1rm_kg`
- AC-2: `app/api/v1/onboard.py` contains `profile.bench_1rm = request.bench_1rm_kg`
- AC-3: `app/api/v1/onboard.py` contains `profile.bodyweight_kg = request.bodyweight_kg`
- AC-4: `app/api/v1/onboard.py` contains `profile.run_5k_seconds = request.run_5k_seconds`
- AC-5: `app/services/state_service.py` `initialize_athlete_state` signature includes `deadlift_1rm_kg`, `bench_1rm_kg`, `bodyweight_kg`, and `run_5k_seconds`

### Rules reminder
- You may only edit files under `app/`, `tests/`, `docs/`.
- Do NOT modify anything under `agent_collab/`.
- When done, write your output to `agent_collab/outbox/coder/` and notify the orchestrator.
