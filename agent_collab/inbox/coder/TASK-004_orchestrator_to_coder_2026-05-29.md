---
task_id: TASK-004
from: orchestrator
to: coder
timestamp: 2026-05-29 00:20
turn: 1
cycle: 1
---

## Assignment: TASK-004 — auth-route-tests

TASK-003 is closed (APPROVED). You are now assigned TASK-004.

**Handoff file:** `agent_collab/handoffs/claimed/TASK-004_auth-route-tests_claimed.md`

### Summary

Create `tests/test_auth_routes.py` with 7 async pytest tests covering the auth route contract:

1. `test_register_success` → POST /auth/register → 201
2. `test_register_duplicate_email` → POST /auth/register (duplicate) → 409
3. `test_register_missing_field` → POST /auth/register (no password) → 422
4. `test_login_success` → POST /auth/token (correct creds) → 200 + access_token
5. `test_login_wrong_password` → POST /auth/token (bad password) → 401
6. `test_login_nonexistent_user` → POST /auth/token (unknown email) → 401
7. `test_me_with_valid_token` → GET /auth/me (Bearer token) → 200 + user fields

### Key constraints

- Use `http_client` fixture from `conftest.py` — no fixture additions needed.
- Token endpoint uses form-encoded data (`username`/`password` fields), not JSON.
- Do NOT modify any file in `app/`, `docs/`, or `pyproject.toml`.
- Only file to create: `tests/test_auth_routes.py`.

### When done

Write your completion report to:
`agent_collab/outbox/coder/TASK-004_coder_to_orchestrator_2026-05-29.md`

Include:
- Confirmation that all 10 ACs are met
- Output of `pytest --collect-only -k "auth_routes"` (showing 7 items collected, no import errors)
- Any notes on pre-existing environment issues (Postgres not available, etc.)
