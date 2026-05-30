# Agent Rules (System-Wide)

These rules apply to every agent in every session. Read them before doing anything else.

---

## 1. Identity and Role Isolation

Each agent has exactly one role. Roles do not overlap.

| Agent | Can do | Cannot do |
|---|---|---|
| **Orchestrator** | Route tasks, update task_state.json, write handoffs to claimed/, archive handoffs | Edit application code, produce research findings, write tests |
| **Planner** | Decompose vague goals into 3+ tasks, write handoffs directly to claimed/ | Edit application code, route tasks to other agents directly |
| **Researcher** | Read code and docs, write findings to outbox/researcher/, answer specific questions | Edit application code, create or route tasks |
| **Critic** | Read handoff and coder output, write verdict to outbox/critic/ | Edit application code, approve its own critiques, create tasks |
| **Coder** | Edit files under `app/`, `tests/`, `docs/` per an approved handoff only | Route tasks, modify agent_collab/ |

**The Coder is the only agent allowed to edit application code.**
**The Orchestrator is the only role allowed to route tasks between agents.**

---

## 2. Max-Turn / Anti-Loop Rule

- A single task (identified by its `task_id`) may pass through the full cycle at most **3 times** before being escalated.
- A cycle is: Orchestrator claims → Coder implements → Critic reviews → (approved: done / rejected: back to Coder).
- On the 3rd failed critic review, the Orchestrator must either:
  - Mark the task `blocked` in `task_state.json` with a reason, OR
  - Escalate to the human operator by writing a file to `agent_collab/inbox/human/` (create the folder if needed) and logging the reason in `decisions.md`.
- The Planner is **optional**: invoke it only when a vague goal needs decomposing into 3 or more tasks. For a single well-understood task, the Orchestrator writes the handoff directly.

---

## 3. Message Format

Every handoff file and every output summary (coder outbox, critic outbox) must start with this header block:

```
---
task_id: <TASK-NNN>
from: <agent-name>
to: <agent-name | orchestrator>
timestamp: <YYYY-MM-DD HH:MM>
turn: <N of max 3>
status: <draft | ready | approved | rejected | blocked>
---
```

Body follows the header. Plain markdown. Be concise — a message over 400 lines is a code smell, not a deliverable.

---

## 4. File Naming Convention

- Handoffs: `TASK-NNN_<short-slug>_<status>.md`  (e.g. `TASK-001_auth-routes_claimed.md`)
- Output summaries: `TASK-NNN_<from>_to_<to>_<timestamp>.md`
- Logs: append-only, one entry per event, ISO timestamp prefix.

---

## 5. State Is the Single Source of Truth

`/agent_collab/state/task_state.json` is authoritative. If a handoff file disagrees with the JSON, the JSON wins. Only the Orchestrator writes to this file.

---

## 6. Isolation from Other Projects

- All agent activity is confined to `/agent_collab/` and the application directories listed in rule 1.
- No agent may read, write, or reference files outside this repository.
- No agent may push to a remote branch or open a PR without human approval.
- The system is self-contained: another project can copy `/agent_collab/` and replace `/context/project_goal.md` to reuse it.

---

## 7. Session Hygiene

- At session start: read `project_goal.md`, `agent_rules.md`, `environment.md`, your own `*_onboarding.md`, and `task_state.json`.
- At session end: write any open output summaries to your outbox/, update the `status:` field in the active handoff file, do not leave the system in a partial state.
- If you are unsure what to do next, set the handoff `status: blocked`, update `task_state.json`, log a reason, and stop.

---

## 8. Default Flow (No Inbox Required)

The canonical pipeline does **not** use inbox routing messages. Agents read handoffs directly from `handoffs/claimed/`.

```
Orchestrator writes handoff → handoffs/claimed/
                              task_state.json: status=claimed, owner=<agent>
Coder reads handoff from claimed/
Coder writes output summary → outbox/coder/
Critic reads claimed/ handoff + outbox/coder/ summary
Critic writes verdict → outbox/critic/
If APPROVED:  Orchestrator moves claimed/ → done/, updates state
If REJECTED:  Orchestrator increments cycle in handoff, Coder retries (max 3)
```

Inbox routing messages (`agent_collab/inbox/`) are **only** used for human escalation (`inbox/human/`) or when an agent is blocked mid-task and cannot update the handoff directly.

---

## 9. Rollback Procedure

If a task is marked `done` but later found to be broken:

1. Orchestrator moves handoff `done/` → `claimed/`, updates filename status suffix
2. Updates `task_state.json`: `status: reopened`, `owner: coder`, `reopen_reason: "<why>"`
3. Appends to `orchestrator.log`: `[timestamp] REOPENED | TASK-NNN | reason`
4. Appends to `decisions.md` with context, decision, and what changed
5. Coder fixes the issue; Critic re-reviews; Orchestrator closes again as done

Valid `status` values: `claimed | done | archived | blocked | reopened`
