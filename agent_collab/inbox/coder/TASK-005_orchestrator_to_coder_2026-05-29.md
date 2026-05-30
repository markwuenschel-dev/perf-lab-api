---
task_id: TASK-005
from: orchestrator
to: coder
timestamp: 2026-05-29 00:30
turn: 1
cycle: 1
---

## Assignment: TASK-005 — dashboard-route-tests

TASK-004 (auth-route-tests) has been approved and closed. You are now assigned TASK-005.

### What to do

Read the full handoff at:
`agent_collab/handoffs/claimed/TASK-005_dashboard-route-tests_claimed.md`

Create `tests/test_dashboard_routes.py` with exactly 5 async test functions covering the dashboard route contract.

### Quick summary

Routes under test (all require Bearer token auth):
- `GET /v1/dashboard/kpis` → 200 `{"kpis": [...], "primary_anchors": [...]}`
- `GET /v1/dashboard/domain-summary?domain=<str>` → 200 `{"domain": str, "kpis": [...], "primary_anchors": [...]}`
- `GET /v1/dashboard/readiness` → 200 `{"state": null|{...}, "kpi_flags": {...}}`

Required tests:
1. `test_dashboard_kpis_authenticated` — 200, kpis + primary_anchors keys present
2. `test_dashboard_domain_summary_authenticated` — 200, domain + kpis + primary_anchors keys present
3. `test_dashboard_readiness_authenticated` — 200, state + kpi_flags keys present
4. `test_dashboard_unauthenticated` — 401 (no token on /v1/dashboard/kpis)
5. `test_dashboard_readiness_no_state_not_500` — 200, `state` is null (JSON null → Python None)

### Key implementation details

- Define a local helper `_register_and_get_token(client, email, password)` — do NOT import from other test files.
- Use `params={"domain": "strength"}` for the domain-summary call.
- A freshly-registered user has no AthleteState rows, so `/readiness` returns `state: null` — assert `resp.json()["state"] is None`.
- All routes are at `/v1/dashboard/...` (router prefix `/dashboard` mounted under `/v1`).
- Use `pytestmark = pytest.mark.asyncio` at module level.

### When done

Write your completion report to:
`agent_collab/outbox/coder/TASK-005_coder_to_orchestrator_2026-05-29.md`

Include your `pytest --collect-only -q tests/test_dashboard_routes.py` output confirming 5 collected, 0 errors.
