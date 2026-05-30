---
task_id: TASK-006
from: orchestrator
to: coder
timestamp: 2026-05-29 00:40
turn: 1
cycle: 1
status: done
needs: coder
assigned_to: coder
---

## Task Title
Add default planning templates for 7 unhandled `BlockGoal` enum values in `app/services/planning_service.py`.

## Goal
The planner currently falls back to the STRENGTH template for all `BlockGoal` values except `STRENGTH` and `RUNNING`. Add proper `_DEFAULT_TEMPLATES` entries for the 7 remaining enum values so each goal gets a meaningful, contextually appropriate weekly slot layout. No changes to tests, app code outside `planning_service.py`, docs, or migrations.

---

## Context: What the Planner Currently Does

### File to edit (one file only)
`app/services/planning_service.py`

### How templates work
`_DEFAULT_TEMPLATES` is a `dict[BlockGoal, list[WeeklyTemplateSlot]]` near the top of the file (lines 18–29). Each entry maps a `BlockGoal` enum value to a list of 3 `WeeklyTemplateSlot` objects — one per default training day.

The fallback (line 33) reads:
```python
slots = _DEFAULT_TEMPLATES.get(goal) or _DEFAULT_TEMPLATES[BlockGoal.STRENGTH]
```
Any `BlockGoal` not present in `_DEFAULT_TEMPLATES` silently falls through to STRENGTH. The 7 goals below are unhandled.

### Existing template structure (copy-paste pattern for all new entries)

```python
_DEFAULT_TEMPLATES: dict[BlockGoal, list[WeeklyTemplateSlot]] = {
    BlockGoal.STRENGTH: [
        WeeklyTemplateSlot(day_of_week=1, category="Max Strength", modality="Strength"),
        WeeklyTemplateSlot(day_of_week=3, category="Strength — Volume", modality="Strength"),
        WeeklyTemplateSlot(day_of_week=5, category="Accessory Focus", modality="Hypertrophy"),
    ],
    BlockGoal.RUNNING: [
        WeeklyTemplateSlot(day_of_week=2, category="Aerobic Base", modality="Running"),
        WeeklyTemplateSlot(day_of_week=4, category="Threshold Work", modality="Running"),
        WeeklyTemplateSlot(day_of_week=6, category="Active Recovery", modality="Running"),
    ],
}
```

Each `WeeklyTemplateSlot` has:
- `day_of_week: int` — 1=Monday through 7=Sunday
- `category: str` — human-readable session label (used as `PlannedSession.category`)
- `modality: str` — broad training modality (used as `PlannedSession.modality`)

`_default_template_for_goal` slices the list to `sessions_per_week`, so keep exactly 3 slots per template (the maximum default).

---

## The 7 Templates to Add

Add these 7 entries to `_DEFAULT_TEMPLATES` immediately after the `BlockGoal.RUNNING` entry.

### BlockGoal.HYPERTROPHY
```python
BlockGoal.HYPERTROPHY: [
    WeeklyTemplateSlot(day_of_week=1, category="High Volume Upper", modality="Hypertrophy"),
    WeeklyTemplateSlot(day_of_week=3, category="High Volume Lower", modality="Hypertrophy"),
    WeeklyTemplateSlot(day_of_week=5, category="Accessory / Isolation", modality="Hypertrophy"),
],
```

### BlockGoal.POWER
```python
BlockGoal.POWER: [
    WeeklyTemplateSlot(day_of_week=1, category="Power Development", modality="Power"),
    WeeklyTemplateSlot(day_of_week=3, category="Strength Potentiation", modality="Strength"),
    WeeklyTemplateSlot(day_of_week=5, category="Neural Priming", modality="Power"),
],
```

### BlockGoal.HYROX
```python
BlockGoal.HYROX: [
    WeeklyTemplateSlot(day_of_week=1, category="Strength Endurance", modality="Mixed"),
    WeeklyTemplateSlot(day_of_week=3, category="Running + Functional", modality="Mixed"),
    WeeklyTemplateSlot(day_of_week=6, category="Hyrox Simulation", modality="Mixed"),
],
```

### BlockGoal.CROSSFIT
```python
BlockGoal.CROSSFIT: [
    WeeklyTemplateSlot(day_of_week=1, category="Strength + Skill", modality="Mixed"),
    WeeklyTemplateSlot(day_of_week=3, category="MetCon", modality="Conditioning"),
    WeeklyTemplateSlot(day_of_week=5, category="Engine Work", modality="Conditioning"),
],
```

### BlockGoal.CALISTHENICS
```python
BlockGoal.CALISTHENICS: [
    WeeklyTemplateSlot(day_of_week=1, category="Skill & Straight-Arm Strength", modality="Calisthenics"),
    WeeklyTemplateSlot(day_of_week=3, category="Bodyweight Strength", modality="Calisthenics"),
    WeeklyTemplateSlot(day_of_week=5, category="Gymnastics Conditioning", modality="Calisthenics"),
],
```

### BlockGoal.GENERAL
```python
BlockGoal.GENERAL: [
    WeeklyTemplateSlot(day_of_week=1, category="Full-Body GPP", modality="General"),
    WeeklyTemplateSlot(day_of_week=3, category="Aerobic + Strength", modality="General"),
    WeeklyTemplateSlot(day_of_week=5, category="Active Recovery", modality="General"),
],
```

### BlockGoal.RECOMP
```python
BlockGoal.RECOMP: [
    WeeklyTemplateSlot(day_of_week=1, category="Strength Preservation", modality="Strength"),
    WeeklyTemplateSlot(day_of_week=3, category="Metabolic Conditioning", modality="Conditioning"),
    WeeklyTemplateSlot(day_of_week=5, category="Active Recovery", modality="General"),
],
```

---

## Implementation Notes

- Edit only `app/services/planning_service.py`.
- Add all 7 entries into the existing `_DEFAULT_TEMPLATES` dict literal (after `BlockGoal.RUNNING`).
- Do NOT change the fallback line, the `_default_template_for_goal` function, or any other function.
- Do NOT add imports — `BlockGoal` and `WeeklyTemplateSlot` are already imported at the top of the file.
- No migrations required — `weekly_template` is a JSONB column, schema-free.
- No test changes required (but verify the file is syntactically valid Python after editing).

---

## Acceptance Criteria

Each criterion is grep-verifiable against `app/services/planning_service.py`:

- [x] AC-1: `grep -c "BlockGoal\." app/services/planning_service.py` — count increases from 4 to 11 (9 enum refs in dict + 2 in functions)
- [x] AC-2: `grep "BlockGoal.HYPERTROPHY" app/services/planning_service.py` — matches at least 1 line
- [x] AC-3: `grep "BlockGoal.POWER" app/services/planning_service.py` — matches at least 1 line
- [x] AC-4: `grep "BlockGoal.HYROX" app/services/planning_service.py` — matches at least 1 line
- [x] AC-5: `grep "BlockGoal.CROSSFIT" app/services/planning_service.py` — matches at least 1 line
- [x] AC-6: `grep "BlockGoal.CALISTHENICS" app/services/planning_service.py` — matches at least 1 line
- [x] AC-7: `grep "BlockGoal.GENERAL" app/services/planning_service.py` — matches at least 1 line
- [x] AC-8: `grep "BlockGoal.RECOMP" app/services/planning_service.py` — matches at least 1 line
- [x] AC-9: `python3 -c "import ast; ast.parse(open('app/services/planning_service.py').read()); print('OK')"` — prints `OK` (no syntax errors)
- [x] AC-10: `_DEFAULT_TEMPLATES` dict contains exactly 9 keys (2 existing + 7 new): verify by counting `BlockGoal.` occurrences inside the dict literal.

---

## Files Edited
- `app/services/planning_service.py` — the ONLY file touched

## Attachments
- coder report: agent_collab/outbox/coder/TASK-006_coder_to_orchestrator_2026-05-29.md
- critique: agent_collab/outbox/critic/TASK-006_critic_to_orchestrator_2026-05-29.md

## Dependencies
TASK-001 (done), TASK-002 (done), TASK-003 (done), TASK-004 (done), TASK-005 (done)

## History
| Timestamp        | Action                                          | By           |
|------------------|-------------------------------------------------|--------------|
| 2026-05-29 00:40 | Created and claimed (skip pending), cycle 1     | orchestrator |
| 2026-05-29 00:40 | Assigned to coder, cycle 1                      | orchestrator |
| 2026-05-29 00:50 | Coder complete: 9-key dict, all ACs met         | coder        |
| 2026-05-29 00:50 | Critic verdict: APPROVED, all 9 checks passed   | critic       |
| 2026-05-29 00:50 | Critic APPROVED. Moved to done.                 | orchestrator |
