# Handoff File Template

Copy this file, rename it `TASK-<NNN>_<slug>.md`, and fill it in.
The Orchestrator moves files between pending/ → claimed/ → done/ → archived/.
Agents never move their own handoff files.

```
---
task_id: TASK-NNN
from: <role that produced this>
to: orchestrator
timestamp: <ISO-8601>
turn: <global turn number>
cycle: <1 | 2 | 3>
status: pending | claimed | done | escalated
needs: researcher | critic | coder
assigned_to: <role currently holding this task, or "none">
---

## Task Title
<one-line>

## Goal
<what must be true when done>

## Context
<relevant file paths, route names, schema fields>

## Acceptance Criteria
- [ ] ...

## Attachments
- findings: outbox/researcher/TASK-NNN_findings.md (when available)
- critique: outbox/critic/TASK-NNN_critique.md (when available)

## Dependencies
<other TASK-IDs that must complete first, or "none">

## History
| Timestamp | Action | By |
|-----------|--------|----|
| ...       | ...    | .. |
```
