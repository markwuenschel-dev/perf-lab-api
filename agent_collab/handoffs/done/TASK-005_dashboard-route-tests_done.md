---
task_id: TASK-005
from: orchestrator
to: coder
timestamp: 2026-05-29 00:30
turn: 1
cycle: 1
status: done
needs: coder
assigned_to: coder
---

## Task Title
Write route contract tests for dashboard routes (`/v1/dashboard/kpis`, `/v1/dashboard/domain-summary`, `/v1/dashboard/readiness`) — 5 test cases covering auth guard and graceful empty-state behavior.

## Goal
Create `tests/test_dashboard_routes.py` with exactly 5 pytest async test functions covering the dashboard route contract. Tests must use the existing `http_client` and `http_client` + auth helper pattern from `conftest.py`. No changes to app code, migrations, or docs.

## Route Contract Reference

**Dashboard routes are registered at `/v1/dashboard/...`** (router prefix `/dashboard` mounted under `/v1` in `app/main.py`).

The router uses `get_current_user` (JWT Bearer) on every endpoint — all three routes require authentication.

### GET /v1/dashboard/kpis
- Request: `Authorization: Bearer <token>` header
- Response 200: `{"kpis": [...], "primary_anchors": [...]}`
  - `kpis`: list of KPI objects (may be empty list when user has no state)
  - `primary_anchors`: list of anchor observation objects (may be empty list)
- Response 401: no token or invalid token

### GET /v1/dashboard/domain-summary
- Request: `Authorization: Bearer <token>` header + query param `domain=<str>` (required, 2–50 chars)
- Response 200: `{"domain": str, "kpis": [...], "primary_anchors": [...]}`
  - Fields may contain empty lists when user has no data for that domain
- Response 401: no token or invalid token
- Response 422: missing `domain` query param

### GET /v1/dashboard/readiness
- Request: `Authorization: Bearer <token>` header
- Response 200: `{"state": null | {...}, "kpi_flags": {...}}`
  - When user has no athlete state rows yet: `state` is `null`, `kpi_flags` is `{"note": "no_athlete_state"}`
  - Must NOT return 500 even with no data
- Response 401: no token or invalid token

## The 5 Required Test Cases

```
test_dashboard_kpis_authenticated
  Register a user, log in, GET /v1/dashboard/kpis with Bearer token
  → 200, body has "kpis" (list) and "primary_anchors" (list)
  (lists may be empty — freshly-registered user has no observations)

test_dashboard_domain_summary_authenticated
  Register a user, log in, GET /v1/dashboard/domain-summary?domain=strength with Bearer token
  → 200, body has "domain" (str), "kpis" (list), "primary_anchors" (list)

test_dashboard_readiness_authenticated
  Register a user, log in, GET /v1/dashboard/readiness with Bearer token
  → 200, body has "state" key and "kpi_flags" key
  (state may be null — freshly-registered user has no AthleteState rows)

test_dashboard_unauthenticated
  GET /v1/dashboard/kpis with no Authorization header
  → 401

test_dashboard_readiness_no_state_not_500
  Register a user, log in, GET /v1/dashboard/readiness with Bearer token
  → must return 200 (not 500), body["state"] is null
  (validates graceful fallback for user with zero AthleteState rows)
```

## Acceptance Criteria

- [x] AC-1: `tests/test_dashboard_routes.py` exists
- [x] AC-2: File contains exactly 5 test functions named as specified above
- [x] AC-3: `test_dashboard_kpis_authenticated` asserts `status_code == 200` and body has `"kpis"` and `"primary_anchors"` keys
- [x] AC-4: `test_dashboard_domain_summary_authenticated` asserts `status_code == 200` and body has `"domain"`, `"kpis"`, `"primary_anchors"` keys
- [x] AC-5: `test_dashboard_readiness_authenticated` asserts `status_code == 200` and body has `"state"` and `"kpi_flags"` keys
- [x] AC-6: `test_dashboard_unauthenticated` asserts `status_code == 401`
- [x] AC-7: `test_dashboard_readiness_no_state_not_500` asserts `status_code == 200` and `resp.json()["state"] is None`
- [x] AC-8: `pytest --collect-only -q tests/test_dashboard_routes.py` collects exactly 5 items with 0 errors

## Files Created
- `tests/test_dashboard_routes.py`

## Attachments
- findings: none
- critique: agent_collab/outbox/critic/TASK-005_critic_to_orchestrator_2026-05-29.md

## Dependencies
TASK-001 (done), TASK-002 (done), TASK-003 (done), TASK-004 (done)

## History
| Timestamp        | Action                                          | By           |
|------------------|-------------------------------------------------|--------------|
| 2026-05-29 00:30 | Created and claimed (skip pending), cycle 1     | orchestrator |
| 2026-05-29 00:30 | Assigned to coder, cycle 1                      | orchestrator |
| 2026-05-29 00:40 | Coder complete: 5 tests, all ACs met            | coder        |
| 2026-05-29 00:40 | Critic verdict: APPROVED, all 8 ACs passed      | critic       |
| 2026-05-29 00:40 | Closed, moved to done/                          | orchestrator |
