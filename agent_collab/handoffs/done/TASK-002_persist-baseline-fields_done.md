---
task_id: TASK-002
from: planner
to: orchestrator
timestamp: 2026-05-29 00:05
turn: 1
cycle: 1
status: done
needs: coder
assigned_to: none
---

## Task Title
Persist all five baseline fields from OnboardRequest into AthleteProfile and pass them to initialize_athlete_state

## Goal
After a successful POST /v1/onboard, the AthleteProfile row must contain non-null values for every baseline field that the client supplied (`deadlift_1rm_kg`, `bench_1rm_kg`, `bodyweight_kg`, `run_5k_seconds`, `squat_1rm_kg`), and `initialize_athlete_state` must receive all five values so S0 reflects the full baseline context.

## Context

**Schema fields accepted by `OnboardRequest`** (`app/schemas/onboarding.py`):
- `squat_1rm_kg` → maps to `AthleteProfile.squat_1rm`
- `deadlift_1rm_kg` → maps to `AthleteProfile.deadlift_1rm`
- `bench_1rm_kg` → maps to `AthleteProfile.bench_1rm`
- `bodyweight_kg` → maps to `AthleteProfile.bodyweight_kg`
- `run_5k_seconds` → maps to `AthleteProfile.run_5k_seconds`

**ORM model** (`app/models/user.py`, class `AthleteProfile`):
| Schema field       | ORM column        | SQLAlchemy type |
|--------------------|-------------------|-----------------|
| `squat_1rm_kg`     | `squat_1rm`       | `Float`         |
| `deadlift_1rm_kg`  | `deadlift_1rm`    | `Float`         |
| `bench_1rm_kg`     | `bench_1rm`       | `Float`         |
| `bodyweight_kg`    | `bodyweight_kg`   | `Float`         |
| `run_5k_seconds`   | `run_5k_seconds`  | `Float`         |

**Current gap** (`app/api/v1/onboard.py`):
- The profile-upsert block (lines 32–38) sets only `experience_years`, `experience_level`, `available_days_per_week`, `session_duration_minutes`, and `equipment`. The five baseline columns are never assigned.
- The `initialize_athlete_state` call (lines 54–58) passes only `squat_1rm_kg`; the other four are dropped.

**Service signature** (`app/services/state_service.py`, line 64):
```python
async def initialize_athlete_state(
    db: AsyncSession,
    user_id: int,
    *,
    experience_level: str = "intermediate",
    squat_1rm_kg: float | None = None,
) -> UnifiedStateVector:
```
The service signature itself must also be extended to accept and forward the four new parameters.

**Files to edit** (Coder may only edit files under `app/`, `tests/`, `docs/`):
1. `app/api/v1/onboard.py` — add five profile assignments; extend `initialize_athlete_state` call
2. `app/services/state_service.py` — extend `initialize_athlete_state` signature and forward params to `_build_baseline_vector`
3. `tests/test_integration_flow.py` (or a new file `tests/test_onboard_baseline_fields.py`) — add/extend a test that posts all five fields and asserts they land in the profile row

## Acceptance Criteria
- [ ] AC-1: `app/api/v1/onboard.py` contains `profile.deadlift_1rm = request.deadlift_1rm_kg` (grep-verifiable)
- [ ] AC-2: `app/api/v1/onboard.py` contains `profile.bench_1rm = request.bench_1rm_kg` (grep-verifiable)
- [ ] AC-3: `app/api/v1/onboard.py` contains `profile.bodyweight_kg = request.bodyweight_kg` (grep-verifiable)
- [ ] AC-4: `app/api/v1/onboard.py` contains `profile.run_5k_seconds = request.run_5k_seconds` (grep-verifiable)
- [ ] AC-5: `app/services/state_service.py` `initialize_athlete_state` signature includes `deadlift_1rm_kg`, `bench_1rm_kg`, `bodyweight_kg`, and `run_5k_seconds` as keyword parameters (grep-verifiable)

## Attachments
- findings: none
- critique: none

## Dependencies
TASK-001 (done)

## History
| Timestamp        | Action                              | By           |
|------------------|-------------------------------------|--------------|
| 2026-05-29 00:05 | Created, status pending             | planner      |
| 2026-05-29 00:05 | Claimed, assigned to coder          | orchestrator |
| 2026-05-29 00:10 | Critic APPROVED. Moved to done.     | orchestrator |
