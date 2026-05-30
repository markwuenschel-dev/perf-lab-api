# Decision Log

Running record of non-obvious Orchestrator decisions. Each entry explains a fork in the road so future sessions understand why the system is in its current state.

---

## Format

```
### [YYYY-MM-DD HH:MM] <decision-title>
**Context:** What situation triggered this decision.
**Decision:** What was decided.
**Rationale:** Why — the constraint, tradeoff, or information that made this the right call.
**Effect:** What changed in task_state.json or the handoff directory as a result.
```

---

### [2026-05-29 00:00] System Initialized
**Context:** Collaboration scaffold created from scratch.
**Decision:** Adopt filesystem-based message passing with task_state.json as single source of truth.
**Rationale:** No external message broker needed; fully portable across projects; human-readable at rest.
**Effect:** task_state.json initialized with empty task list. All handoff directories empty.

### [2026-05-29 01:00] TASK-007 findings accepted — exercise selection research
**Context:** Prescriber uses hardcoded _EQUIPMENT_EXERCISE_MAP; Exercise ORM table exists with 8 filterable columns.
**Decision:** Accept findings. DB-driven exercise selection requires async pre-fetch at route handler level (recommend_next_session is sync — cannot easily be made async without breaking 11+ test call sites).
**Rationale:** Safe migration path identified. Defer implementation to a future task after TASK-008.
**Effect:** TASK-007 done. TASK-008 (weak-point routes) is the final backlog task.

### [2026-05-29 01:10] Full backlog sprint complete
**Context:** 8 tasks planned, all 8 completed in a single session.
**Decision:** All tasks APPROVED by Critic. No escalations, no blocked tasks, 0 cycle-2 retries needed.
**Rationale:** n/a — sprint complete.
**Effect:** task_state.json: all 8 tasks status=done. Remaining work: DB-driven exercise selection (needs TASK-007 findings), frontend routes, deload scaling (Phase 3 ROADMAP items).

### [2026-05-29 01:10] TASK-008 APPROVED and DONE — weak-point-routes
**Context:** Coder delivered `app/api/v1/weak_points.py`, updated `app/main.py`, and created `tests/test_weak_point_routes.py`. Critic verified all 10 ACs.
**Decision:** APPROVED. Moved handoff claimed→done.
**Rationale:** All syntax checks passed; 6 async tests confirmed via AST walk; all three routes filter by `current_user.id`; router import and include_router both present in main.py.
**Effect:** TASK-008 status=done, owner=null. All 8 backlog tasks are now done.

### [2026-05-29 00:04] TASK-001 closed — remove-dev-user-override
**Context:** Privilege escalation: any authenticated user could pass ?user_id= to read another user's prescription data.
**Decision:** Removed DEV ONLY query param override; all DB queries now use current_user.id directly. New test test_next_session_ignores_user_id_query_param added.
**Rationale:** Security fix, highest priority. Smallest scoped change for first pipeline run.
**Effect:** TASK-001 status=done. 7 tasks remain in the proposed backlog.
