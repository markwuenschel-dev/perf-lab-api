---
task_id: TASK-008
from: orchestrator
to: coder
timestamp: 2026-05-29 01:00
turn: 1
cycle: 1
status: done
needs: coder
assigned_to: coder
---

## Task Title
Implement standalone weak-point management API routes so the frontend can surface and manage the weak-point layer without going through benchmark observations.

## Goal
The `WeakPoint` ORM model already exists at `app/models/weak_point.py`. There are currently no API routes to list, update, or delete weak-point rows. This task adds three routes in a new file `app/api/v1/weak_points.py`, registers the router in `app/main.py`, and writes tests in `tests/test_weak_point_routes.py`.

Do NOT modify `app/logic/`, `app/services/`, `app/schemas/prescription.py`, or any existing test file.

---

## Context

### WeakPoint ORM model — `app/models/weak_point.py`

Table: `weak_points`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | Integer | NOT NULL | Primary key, indexed |
| `user_id` | Integer (FK → users.id) | NOT NULL | Indexed; scopes all queries |
| `tag` | String | NOT NULL | Canonical tag from `WEAK_POINT_TAGS`; indexed |
| `source` | SAEnum(WeakPointSource) | NOT NULL | `"self_report"`, `"benchmark"`, `"inference"`, `"performance_data"` |
| `confidence` | Float | NOT NULL | Default 0.5; range 0.0–1.0 |
| `note` | Text | nullable | Optional human-readable note |
| `detected_at` | DateTime | NOT NULL | Default utcnow |
| `resolved_at` | DateTime | nullable | NULL = active; non-NULL = resolved |
| `source_session_id` | Integer (FK → planned_sessions.id) | nullable | Set when source=benchmark |

`WeakPoint.is_active` is a `@property`: `return self.resolved_at is None`.

`WeakPointSource` enum values (str enum): `self_report`, `benchmark`, `inference`, `performance_data`.

`WEAK_POINT_TAGS` — list of 29 canonical string tags defined at `app/models/weak_point.py:21–57`.

### Router pattern — `app/api/v1/dashboard.py`

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User

router = APIRouter(prefix="/weak-points", tags=["Weak Points"])

@router.get("/", ...)
async def list_weak_points(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
): ...
```

### Router registration — `app/main.py`

Pattern used by all v1 routers (line 129–133):
```python
from app.api.v1 import weak_points
# ...
app.include_router(weak_points.router, prefix=settings.API_V1_STR)
```

Note: `app/main.py:137` already contains a commented-out stub:
```python
# app.include_router(weak_points.router, prefix=settings.API_V1_STR)
```
Uncomment and add the import — do not add a duplicate line.

### Auth pattern
Every protected endpoint uses:
```python
current_user: User = Depends(get_current_user)
```
All DB queries must filter by `current_user.id` (never accept user_id from query params).

---

## Exact Route Specifications

### Route 1: GET /v1/weak-points

**Path:** `GET /v1/weak-points`
**Auth:** Required (`get_current_user`)
**Query params:** `active_only: bool = True` (when True, filter `resolved_at IS NULL`)
**DB query:** `SELECT * FROM weak_points WHERE user_id = current_user.id [AND resolved_at IS NULL]`
**Response model:** `list[WeakPointOut]`
**Status codes:**
- `200 OK` — returns list (may be empty)
- `401 Unauthorized` — no/invalid token (handled by `get_current_user` dependency)

**`WeakPointOut` schema** (define in `app/api/v1/weak_points.py` as a Pydantic `BaseModel`):
```python
class WeakPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tag: str
    source: str          # WeakPointSource value as string
    confidence: float
    note: str | None
    detected_at: datetime
    resolved_at: datetime | None
    is_active: bool
```

---

### Route 2: PATCH /v1/weak-points/{id}

**Path:** `PATCH /v1/weak-points/{id}`
**Auth:** Required (`get_current_user`)
**Path param:** `id: int` — the WeakPoint row id
**Request body:** `WeakPointPatch` (define in `app/api/v1/weak_points.py`):
```python
class WeakPointPatch(BaseModel):
    confidence: float | None = None    # 0.0–1.0; validate with Field(ge=0.0, le=1.0)
    note: str | None = None
    resolved_at: datetime | None = None   # pass datetime to resolve; pass null to re-open
```
**DB query:** `SELECT FROM weak_points WHERE id = {id} AND user_id = current_user.id`
**Response model:** `WeakPointOut`
**Status codes:**
- `200 OK` — patch applied, returns updated row
- `404 Not Found` — row not found or belongs to a different user (raise `HTTPException(status_code=404, detail="Weak point not found")`)
- `401 Unauthorized` — no/invalid token

**Implementation notes:**
- Only apply fields that are explicitly set (non-None) in the request body.
- For `resolved_at`: if the client sends `null` (Python `None`) it means re-open (set `resolved_at = None`). If the client sends a datetime it means resolve. Because `None` is also the default, use `model_fields_set` to detect explicit null: apply `resolved_at` only if `"resolved_at"` is in `patch.model_fields_set`.
- Commit with `await db.commit(); await db.refresh(wp)` before returning.

---

### Route 3: DELETE /v1/weak-points/{id}

**Path:** `DELETE /v1/weak-points/{id}`
**Auth:** Required (`get_current_user`)
**Path param:** `id: int`
**DB query:** `SELECT FROM weak_points WHERE id = {id} AND user_id = current_user.id`
**Response:** `204 No Content` (use `Response` with `status_code=204`, return nothing)
**Status codes:**
- `204 No Content` — deleted
- `404 Not Found` — row not found or belongs to a different user
- `401 Unauthorized` — no/invalid token

---

## Files to Create / Edit

### New file: `app/api/v1/weak_points.py`

Full content must include:
1. `WeakPointOut` Pydantic model (with `model_config = ConfigDict(from_attributes=True)`)
2. `WeakPointPatch` Pydantic model
3. `router = APIRouter(prefix="/weak-points", tags=["Weak Points"])`
4. `GET /` handler → `list_weak_points`
5. `PATCH /{id}` handler → `patch_weak_point`
6. `DELETE /{id}` handler → `delete_weak_point`

Imports needed:
```python
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.models.weak_point import WeakPoint
```

### Edit: `app/main.py`

Two changes:
1. Add `weak_points` to the existing import line (line 16):
   ```python
   from app.api.v1 import auth, benchmarks, dashboard, ingest, legacy, planning, prescribe, weak_points
   ```
2. Uncomment the existing stub (line 137) — change:
   ```python
   # app.include_router(weak_points.router, prefix=settings.API_V1_STR)
   ```
   to:
   ```python
   app.include_router(weak_points.router, prefix=settings.API_V1_STR)
   ```

### New file: `tests/test_weak_point_routes.py`

Use the same `AsyncClient` + `override_dependencies` pattern from existing route tests (e.g. `tests/conftest.py` mock pattern).

The test file must include these 6 test functions (one per route × 2 status codes):

```
test_list_weak_points_returns_200_for_authenticated_user
test_list_weak_points_returns_401_for_unauthenticated_request
test_patch_weak_point_returns_200_on_valid_update
test_patch_weak_point_returns_404_for_missing_id
test_delete_weak_point_returns_204_on_success
test_delete_weak_point_returns_404_for_missing_id
```

Tests must be importable without a live DB (use mock/override pattern).

---

## Acceptance Criteria

Each is binary-verifiable:

- [ ] AC-1: `GET /v1/weak-points` returns `200` for authenticated request (test passes)
- [ ] AC-2: `GET /v1/weak-points` returns `401` for unauthenticated request (test passes)
- [ ] AC-3: `PATCH /v1/weak-points/{id}` returns `200` for valid id + auth (test passes)
- [ ] AC-4: `PATCH /v1/weak-points/{id}` returns `404` when id not found (test passes)
- [ ] AC-5: `DELETE /v1/weak-points/{id}` returns `204` for valid id + auth (test passes)
- [ ] AC-6: `DELETE /v1/weak-points/{id}` returns `404` when id not found (test passes)
- [ ] AC-7: `python3 -m pytest tests/test_weak_point_routes.py --collect-only -q` exits 0 with exactly 6 items collected
- [ ] AC-8: `python3 -c "import ast; ast.parse(open('app/api/v1/weak_points.py').read()); print('OK')"` prints `OK`
- [ ] AC-9: `grep "weak_points" app/main.py` matches at least 2 lines (import + include_router)
- [ ] AC-10: No changes to any file outside `app/api/v1/weak_points.py`, `app/main.py`, `tests/test_weak_point_routes.py`

---

## Files NOT to Touch
- `app/logic/` — no changes
- `app/services/` — no changes
- `app/models/` — no changes
- `app/schemas/` — no changes (schemas are defined inline in `weak_points.py`)
- `docs/` — no changes
- Any existing test file other than the new `tests/test_weak_point_routes.py`

---

## Dependencies
TASK-001 (done), TASK-002 (done), TASK-003 (done), TASK-004 (done), TASK-005 (done), TASK-006 (done), TASK-007 (done)

## History
| Timestamp        | Action                                          | By           |
|------------------|-------------------------------------------------|--------------|
| 2026-05-29 01:00 | Created and claimed (skip pending), cycle 1     | orchestrator |
| 2026-05-29 01:00 | Assigned to coder, cycle 1                      | orchestrator |
| 2026-05-29 01:10 | Critic APPROVED. Moved to done.                 | orchestrator |
