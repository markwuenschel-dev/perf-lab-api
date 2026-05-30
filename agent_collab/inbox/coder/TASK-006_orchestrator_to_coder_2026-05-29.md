---
task_id: TASK-006
from: orchestrator
to: coder
timestamp: 2026-05-29 00:40
---

## Assignment: TASK-006 — block-goal-templates

You have been assigned TASK-006. Please read the full handoff:

`agent_collab/handoffs/claimed/TASK-006_block-goal-templates_claimed.md`

### TL;DR

Edit **one file only**: `app/services/planning_service.py`

Add 7 new entries to the `_DEFAULT_TEMPLATES` dict (after `BlockGoal.RUNNING`), one for each unhandled `BlockGoal` value:
- `BlockGoal.HYPERTROPHY`
- `BlockGoal.POWER`
- `BlockGoal.HYROX`
- `BlockGoal.CROSSFIT`
- `BlockGoal.CALISTHENICS`
- `BlockGoal.GENERAL`
- `BlockGoal.RECOMP`

The exact slot content and structure for each is provided in the handoff. Pattern-match from the existing `BlockGoal.STRENGTH` or `BlockGoal.RUNNING` entries — same `WeeklyTemplateSlot(day_of_week=..., category=..., modality=...)` form.

No migrations, no test changes, no new imports needed.

### When done
Write your completion report to:
`agent_collab/outbox/coder/TASK-006_coder_to_orchestrator_2026-05-29.md`
