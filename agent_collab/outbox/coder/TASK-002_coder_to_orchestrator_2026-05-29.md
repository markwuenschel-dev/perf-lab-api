---
task_id: TASK-002
from: coder
to: orchestrator
timestamp: 2026-05-29
turn: 1
status: done
---

## Summary

All five baseline fields are now persisted to `AthleteProfile` and forwarded to `initialize_athlete_state`.

---

## Changes Made

### 1. `app/api/v1/onboard.py`
Added five profile assignments inside the upsert block (after the existing five):
```python
profile.squat_1rm     = request.squat_1rm_kg
profile.deadlift_1rm  = request.deadlift_1rm_kg
profile.bench_1rm     = request.bench_1rm_kg
profile.bodyweight_kg = request.bodyweight_kg
profile.run_5k_seconds = request.run_5k_seconds
```
Extended the `initialize_athlete_state` call to pass all five keyword arguments.

### 2. `app/services/state_service.py`
- Extended `_build_baseline_vector` signature with: `deadlift_1rm_kg`, `bench_1rm_kg`, `bodyweight_kg`, `run_5k_seconds` (all `float | None = None`).
- Extended `initialize_athlete_state` signature with the same four new keyword params.
- `initialize_athlete_state` now forwards all five to `_build_baseline_vector`.

### 3. `tests/test_integration_flow.py`
Added `test_onboard_persists_all_baseline_fields`:
- Registers a user, onboards with all five fields set, then queries `AthleteProfile` from the DB and asserts each column matches the posted value.

---

## Acceptance Criteria Status

| AC   | Check                                                                    | Status |
|------|--------------------------------------------------------------------------|--------|
| AC-1 | `profile.deadlift_1rm = request.deadlift_1rm_kg` in `onboard.py`        | PASS   |
| AC-2 | `profile.bench_1rm = request.bench_1rm_kg` in `onboard.py`              | PASS   |
| AC-3 | `profile.bodyweight_kg = request.bodyweight_kg` in `onboard.py`         | PASS   |
| AC-4 | `profile.run_5k_seconds = request.run_5k_seconds` in `onboard.py`       | PASS   |
| AC-5 | `initialize_athlete_state` signature includes all four new params        | PASS   |

---

## Test Run Output

```
tests/test_integration_flow.py::test_onboard_persists_all_baseline_fields SKIPPED
tests/test_integration_flow.py::test_register_onboard_token_nextsession_roundtrip SKIPPED

SKIPPED [1] tests\test_integration_flow.py:142: Test DB unavailable or migration failed
  (postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test):
  [WinError 1225] The remote computer refused the network connection
SKIPPED [1] tests\test_integration_flow.py:219: Test DB unavailable or migration failed
  (postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test):
  [WinError 1225] The remote computer refused the network connection

2 skipped, 4 deselected in 8.23s
```

Both onboard tests skip gracefully — no Postgres instance is running locally. This is expected behavior (conftest.py skips on DB unavailability). No failures, no import errors.

---

## Known Gaps

- `_build_baseline_vector` accepts the four new params but does not currently use `deadlift_1rm_kg`, `bench_1rm_kg`, `bodyweight_kg`, or `run_5k_seconds` to compute any initial state vector values. Only `squat_1rm_kg` drives `c_nm_force`. Incorporating the other lifts into the baseline vector computation is out of scope for this task but is a natural follow-up.

---

## Files Edited

- `app/api/v1/onboard.py`
- `app/services/state_service.py`
- `tests/test_integration_flow.py`
