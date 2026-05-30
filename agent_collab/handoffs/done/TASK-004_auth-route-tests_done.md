---
task_id: TASK-004
from: orchestrator
to: coder
timestamp: 2026-05-29 00:20
turn: 1
cycle: 1
status: done
needs: coder
assigned_to: coder
---

## Task Title
Write route contract tests for auth routes (`/auth/register`, `/auth/token`, `/auth/me`) — 7 test cases covering the complete happy/sad-path surface.

## Goal
Create `tests/test_auth_routes.py` with exactly 7 pytest async test functions covering the auth route contract. Tests must use the existing `http_client` fixture from `conftest.py` (which already injects the test DB and overrides `get_db`). No changes to app code, migrations, or docs.

## Route Contract Reference

**Auth routes are registered at `/auth/...`** (router prefix is `/auth`, mounted at app root in `app/main.py`).

### POST /auth/register
- Request: JSON body `{"email": str, "password": str}`
  - Password validated: min 8 chars, max 72 chars
- Response 201: `{"id": int, "email": str, "is_active": bool}`
- Response 409: `{"detail": "Email already registered"}` — duplicate email
- Response 422: FastAPI validation error — missing required field

### POST /auth/token
- Request: `application/x-www-form-urlencoded` with `username` (email) and `password`
- Response 200: `{"access_token": str, "token_type": "bearer"}`
- Response 401: `{"detail": "Incorrect email or password"}` — wrong password or non-existent user

### GET /auth/me
- Request: `Authorization: Bearer <token>` header
- Response 200: `{"id": int, "email": str, "is_active": bool}`

## The 7 Required Test Cases

```
test_register_success
  POST /auth/register with valid email + password
  → 201, body contains id (int), email (str), is_active (bool)

test_register_duplicate_email
  Register same email twice
  → second call returns 409

test_register_missing_field
  POST /auth/register with body {"email": "x@example.com"} (no password)
  → 422

test_login_success
  Register a user, then POST /auth/token with correct credentials
  → 200, body contains "access_token" key

test_login_wrong_password
  Register a user, then POST /auth/token with wrong password
  → 401

test_login_nonexistent_user
  POST /auth/token for an email that was never registered
  → 401

test_me_with_valid_token
  Register + login to get a token, GET /auth/me with Authorization: Bearer <token>
  → 200, body contains "id", "email", "is_active"
```

## Acceptance Criteria

- [x] AC-1: `tests/test_auth_routes.py` exists
- [x] AC-2: File contains exactly 7 test functions named as specified above
- [x] AC-3: `test_register_success` asserts `status_code == 201` and body has `id`, `email`, `is_active`
- [x] AC-4: `test_register_duplicate_email` asserts second registration returns `status_code == 409`
- [x] AC-5: `test_register_missing_field` asserts `status_code == 422`
- [x] AC-6: `test_login_success` asserts `status_code == 200` and `"access_token" in response.json()`
- [x] AC-7: `test_login_wrong_password` asserts `status_code == 401`
- [x] AC-8: `test_login_nonexistent_user` asserts `status_code == 401`
- [x] AC-9: `test_me_with_valid_token` asserts `status_code == 200` and body has `id`, `email`, `is_active`
- [x] AC-10: `pytest --collect-only -k "auth_routes"` collects exactly 7 items with no import errors

## Files Created
- `tests/test_auth_routes.py`

## Attachments
- findings: none
- critique: agent_collab/outbox/critic/TASK-004_critic_to_orchestrator_2026-05-29.md

## Dependencies
TASK-001 (done), TASK-002 (done), TASK-003 (done)

## History
| Timestamp        | Action                                        | By           |
|------------------|-----------------------------------------------|--------------|
| 2026-05-29 00:20 | Created and claimed (skip pending)            | orchestrator |
| 2026-05-29 00:20 | Assigned to coder, cycle 1                    | orchestrator |
| 2026-05-29 00:30 | Coder complete: 7 tests, all ACs met          | coder        |
| 2026-05-29 00:30 | Critic verdict: APPROVED, all 10 ACs passed   | critic       |
| 2026-05-29 00:30 | Closed, moved to done/                        | orchestrator |
