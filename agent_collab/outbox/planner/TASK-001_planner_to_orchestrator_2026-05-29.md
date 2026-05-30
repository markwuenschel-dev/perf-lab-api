---
task_id: TASK-001
from: planner
to: orchestrator
timestamp: 2026-05-29 00:00
turn: 1
status: ready
---

## Summary

Handoff written for TASK-001: remove the DEV ONLY `?user_id=` query-parameter privilege
escalation from `GET /v1/next-session`.

## Handoff Location

`agent_collab/handoffs/pending/TASK-001_remove-dev-user-override_pending.md`

## What was found

- **File:** `app/api/v1/prescribe.py` (132 lines)
- **Vulnerability:** Line 26 declares `user_id: int | None = Query(None, description="DEV ONLY — remove in production")` and line 30 sets `effective_user_id = user_id or current_user.id`. Every DB query in the function uses `effective_user_id`, allowing any authenticated user to retrieve another user's prescription data, athlete state, weak points, and planned sessions by passing `?user_id=<target>`.
- **Test file confirmed:** `tests/test_prescribe_routes.py` exists. It contains 8 existing tests; none cover the privilege-escalation path.

## Action Required

Route this handoff to the Coder. No researcher pass is needed — the vulnerability is
fully characterised and the exact lines to change are specified in the handoff.

## Needs

`coder`

## Cycle

1 of 3
