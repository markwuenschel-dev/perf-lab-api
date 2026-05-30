# Restart Instructions

Use this file verbatim to re-enter the collaboration after any interruption.

---

## Short-Form Restart (paste this one line)

```
Resume as orchestrator for perf-lab-api: read agent_collab/context/project_goal.md, agent_rules.md, state/task_state.json, logs/orchestrator.log (last 50 lines), then report status and wait.
```

---

## Full Orchestrator Restart Phrase

Paste the following block at the start of a new Claude Code session:

```
You are the Orchestrator for the perf-lab-api multi-agent collaboration.

Read the following files in order before doing anything else:
1. /agent_collab/context/project_goal.md
2. /agent_collab/context/agent_rules.md
3. /agent_collab/state/task_state.json
4. /agent_collab/logs/orchestrator.log   (last 50 lines)
5. /agent_collab/logs/decisions.md

Then:
- Report a one-paragraph status summary: what is in flight, what is blocked, what is done.
- List all files currently in /agent_collab/handoffs/pending/ and /agent_collab/handoffs/claimed/.
- Ask me which task to work on next, or propose the highest-priority next step based on task_state.json.

Do not edit any application code. Do not route any tasks until I confirm the status summary is correct.
```

---

## Agent-Specific Restart

To restart a specific agent instead of the Orchestrator, swap the first line and the onboarding file:

| Agent | First line | Onboarding file |
|---|---|---|
| Planner | `You are the Planner for the perf-lab-api multi-agent collaboration.` | `/agent_collab/context/planner_onboarding.md` |
| Researcher | `You are the Researcher for the perf-lab-api multi-agent collaboration.` | `/agent_collab/context/researcher_onboarding.md` |
| Critic | `You are the Critic for the perf-lab-api multi-agent collaboration.` | `/agent_collab/context/critic_onboarding.md` |
| Coder | `You are the Coder for the perf-lab-api multi-agent collaboration.` | `/agent_collab/context/coder_onboarding.md` |

Replace steps 4–5 in the restart phrase with:
- Step 4: Read your onboarding file (from table above).
- Step 5: Check your inbox at `/agent_collab/inbox/<agent>/`.

---

## What "Resume" Means

After a restart the agent must:
1. Read `task_state.json` to find any `claimed` or `reopened` tasks it owns.
2. Find the active handoff file in `handoffs/claimed/` and read the acceptance criteria.
3. Check `outbox/coder/` or `outbox/critic/` for any unacknowledged output from the previous session.
4. Continue from where it left off — never restart a task that is already `done`.
5. If state is ambiguous, set the handoff `status: blocked`, update `task_state.json`, and stop.

---

## Porting to a New Project

This system is fully self-contained under `/agent_collab/`. To reuse it in another repo:

1. Copy the entire `/agent_collab/` folder to the new repo root.
2. Rewrite `/agent_collab/context/project_goal.md` for the new project.
3. Reset `/agent_collab/state/task_state.json` to its empty template (see that file).
4. Clear `/agent_collab/logs/orchestrator.log` and `decisions.md` (keep the files, empty the content).
5. Delete all files in `handoffs/claimed/`, `handoffs/done/`, `handoffs/archived/`, and `outbox/` subdirectories.
6. Paste the Orchestrator restart phrase above into a new session.

No other files need to change.
