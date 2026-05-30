---
task_id: TASK-008
from: orchestrator
to: coder
timestamp: 2026-05-29 01:00
---

## Assignment: TASK-008 — weak-point-routes

You have been assigned TASK-008. Please read the full handoff:

`agent_collab/handoffs/claimed/TASK-008_weak-point-routes_claimed.md`

### TL;DR

Create **one new route file** and **one new test file**, then make **two small edits** to `app/main.py`.

**Files to create:**
- `app/api/v1/weak_points.py` — router with 3 endpoints (GET list, PATCH, DELETE)
- `tests/test_weak_point_routes.py` — 6 test functions (2 per route × 3 routes)

**File to edit:**
- `app/main.py` — add `weak_points` to the existing import on line 16; uncomment the already-stubbed `include_router` line at line 137

**Routes to implement:**
1. `GET /v1/weak-points` — list weak points for current user (`active_only=True` default)
2. `PATCH /v1/weak-points/{id}` — update `confidence`, `note`, or `resolved_at`
3. `DELETE /v1/weak-points/{id}` — hard-delete a weak-point row, returns 204

All routes are auth-protected via `Depends(get_current_user)`. All DB queries must filter by `current_user.id`.

Define `WeakPointOut` and `WeakPointPatch` Pydantic schemas inline in `app/api/v1/weak_points.py` — do NOT add to `app/schemas/`.

The WeakPoint ORM model is at `app/models/weak_point.py`. The router pattern to follow is `app/api/v1/dashboard.py`.

### When done
Write your completion report to:
`agent_collab/outbox/coder/TASK-008_coder_to_orchestrator_2026-05-29.md`
