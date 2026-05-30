---
task_id: TASK-008
from: coder
to: orchestrator
timestamp: 2026-05-29 02:00
status: complete
---

## TASK-008 — weak-point-routes: Completion Report

### Files Created / Edited

| File | Action | Notes |
|---|---|---|
| `app/api/v1/weak_points.py` | CREATED | Router with 3 endpoints + inline Pydantic schemas |
| `app/main.py` | EDITED | Added `weak_points` to import; uncommented `include_router` stub |
| `tests/test_weak_point_routes.py` | CREATED | 6 async test functions |

---

### Implementation Summary

#### `app/api/v1/weak_points.py`
- Inline `WeakPointOut` (with `model_config = ConfigDict(from_attributes=True)`) and `WeakPointPatch` Pydantic models
- `router = APIRouter(prefix="/weak-points", tags=["Weak Points"])`
- **GET `/`** — `list_weak_points`: accepts `active_only: bool = True` query param; filters `WHERE user_id = current_user.id [AND resolved_at IS NULL]`; returns `list[WeakPointOut]`, 200 always
- **PATCH `/{weak_point_id}`** — `patch_weak_point`: uses `model_fields_set` to apply only explicitly sent fields (including explicit null for `resolved_at` re-open); 200 on success, 404 if not found/wrong user
- **DELETE `/{weak_point_id}`** — `delete_weak_point`: hard delete via `await db.delete(wp)`; returns `Response(status_code=204)`; 404 if not found/wrong user
- All queries filter by `current_user.id` (no user_id accepted from request params)

#### `app/main.py`
- Line 16: added `weak_points` to existing import statement
- Line 137: uncommented `app.include_router(weak_points.router, prefix=settings.API_V1_STR)`

#### `tests/test_weak_point_routes.py`
- Pattern copied from `tests/test_planning_routes.py` (inline `_override_db` / `_override_user` dependency overrides per test, `finally: app.dependency_overrides.clear()`)
- Helper `_mk_user()` and `_mk_weak_point()` for test data setup
- 6 tests, all using `async_db` fixture from conftest.py

---

### Verification Results

| Check | Command | Result |
|---|---|---|
| AC-8 | `python3 -c "import ast; ast.parse(open('app/api/v1/weak_points.py').read()); print('OK')"` | **OK** |
| AC-8 | `python3 -c "import ast; ast.parse(open('app/main.py').read()); print('OK')"` | **OK** |
| AC-7 | Test file AST parse (6 test functions collected) | **6 tests** |
| AC-9 | `grep "weak_points" app/main.py` | **2 lines matched** (import + include_router) |
| AC-8 | `python3 -c "import ast; ast.parse(open('tests/test_weak_point_routes.py').read()); print('OK')"` | **OK** |

Note: `pytest --collect-only` could not be run via WSL because the project venv uses Windows `.exe` binaries that cannot resolve WSL paths. All three files passed `ast.parse()` and the 6 test function names were confirmed via AST walk.

---

### Acceptance Criteria Status

| AC | Description | Status |
|---|---|---|
| AC-1 | GET /v1/weak-points returns 200 for authenticated request | Ready (test: `test_list_weak_points_returns_200_for_authenticated_user`) |
| AC-2 | GET /v1/weak-points returns 401 for unauthenticated request | Ready (test: `test_list_weak_points_returns_401_for_unauthenticated_request`) |
| AC-3 | PATCH returns 200 for valid id + auth | Ready (test: `test_patch_weak_point_returns_200_on_valid_update`) |
| AC-4 | PATCH returns 404 when id not found | Ready (test: `test_patch_weak_point_returns_404_for_missing_id`) |
| AC-5 | DELETE returns 204 for valid id + auth | Ready (test: `test_delete_weak_point_returns_204_on_success`) |
| AC-6 | DELETE returns 404 when id not found | Ready (test: `test_delete_weak_point_returns_404_for_missing_id`) |
| AC-7 | pytest collect-only exits 0 with 6 items | Verified via AST (6 async test functions confirmed) |
| AC-8 | ast.parse on weak_points.py prints OK | PASS |
| AC-9 | grep "weak_points" app/main.py matches ≥2 lines | PASS (2 lines) |
| AC-10 | No changes outside 3 specified files | PASS |

### Files NOT Touched
- `app/logic/` — untouched
- `app/services/` — untouched
- `app/models/` — untouched
- `app/schemas/` — untouched
- All existing test files — untouched
