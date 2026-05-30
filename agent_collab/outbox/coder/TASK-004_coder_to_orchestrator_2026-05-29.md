---
task_id: TASK-004
from: coder
to: orchestrator
timestamp: 2026-05-29
turn: 2
cycle: 1
status: complete
---

## TASK-004 Completion Report — auth-route-tests

### Summary

`tests/test_auth_routes.py` has been created with exactly 7 async test functions covering the full auth route contract.

### Acceptance Criteria Status

- [x] AC-1: `tests/test_auth_routes.py` exists
- [x] AC-2: File contains exactly 7 test functions named as specified
- [x] AC-3: `test_register_success` asserts `status_code == 201` and body has `id`, `email`, `is_active`
- [x] AC-4: `test_register_duplicate_email` asserts second registration returns `status_code == 409`
- [x] AC-5: `test_register_missing_field` asserts `status_code == 422`
- [x] AC-6: `test_login_success` asserts `status_code == 200` and `"access_token" in response.json()`
- [x] AC-7: `test_login_wrong_password` asserts `status_code == 401`
- [x] AC-8: `test_login_nonexistent_user` asserts `status_code == 401`
- [x] AC-9: `test_me_with_valid_token` asserts `status_code == 200` and body has `id`, `email`, `is_active`
- [x] AC-10: `pytest --collect-only` collects exactly 7 items with no import errors

### Pytest Collect Output

Command: `.venv\Scripts\python.exe -m pytest tests/test_auth_routes.py --collect-only -q`

```
tests/test_auth_routes.py::test_register_success
tests/test_auth_routes.py::test_register_duplicate_email
tests/test_auth_routes.py::test_register_missing_field
tests/test_auth_routes.py::test_login_success
tests/test_auth_routes.py::test_login_wrong_password
tests/test_auth_routes.py::test_login_nonexistent_user
tests/test_auth_routes.py::test_me_with_valid_token

7 tests collected in 0.01s
```

### Implementation Notes

- Used `pytestmark = pytest.mark.asyncio` at module level, consistent with `test_ingest_routes.py` and `test_prescribe_routes.py`.
- All 7 tests are plain `async def` functions (no per-test decorator needed; `asyncio_mode = "auto"` is set in `pyproject.toml`).
- Token endpoint uses `data={"username": email, "password": password}` with `Content-Type: application/x-www-form-urlencoded` header, matching the `OAuth2PasswordRequestForm` dependency in `auth.py`.
- Each test that creates a user uses a unique email address to avoid intra-test 409 collisions.
- No imports from other test files; no helper function added (calls are inlined per the handoff spec).
- Only file created: `tests/test_auth_routes.py`.

### Pre-existing Environment Notes

- Running pytest via WSL bash requires using `cmd.exe /c` with the Windows `.venv\Scripts\python.exe -m pytest` invocation. Using the `.exe` directly from WSL fails because the working directory is not properly resolved to the project root, causing `ModuleNotFoundError: No module named 'app'`. Running as `python -m pytest` from the project root (CMD) resolves this.
- `pytest --collect-only -k auth_routes` across the full suite shows 7 auth route tests collected plus 6 pre-existing collection errors in other test modules (`test_constraint_engine.py`, `test_dose_engine.py`, `test_planning_routes.py`, `test_prescriber_candidates.py`, `test_prescriber_finalize.py`, `test_scenarios.py`). These errors are unrelated to TASK-004 — they stem from missing `numpy` in the venv and a missing `encode_session_candidate` export from `app.logic.constraint_engine`. No Postgres was available during this session; DB-dependent tests would be skipped gracefully per the `async_db` fixture's skip logic.
