---
status: accepted
date: 2026-10-12
---
# A planned session has three days: its block slot, its plan weekday and its scheduled date

Relates to [ADR-0060](0060-objective-mix-live-receding-horizon-microcycle.md) (block week =
`block_id + week_number`), [ADR-0069](0069-date-move-does-not-change-session-status.md) (a move
changes the date) and [ADR-0073](0073-planned-week-projection.md) (block-relative weeks).

## Context

Block creation places a session on `start_date + (week - 1) * 7 + (slot - 1)`
(`app/services/planning_service.py`, `create_block_with_sessions`). A template slot's
`day_of_week` is therefore **day N of the block week**, counted from the block's start date: on a
block that starts on a Thursday, slot 1 is every Thursday. Sessions stored that slot as their own
`day_of_week`, under a column comment that said `1=Mon, 7=Sun`, and the block-create preview
labelled slots Monday-Sunday. Both were true only for blocks starting on a Monday, and the
create form defaults the start date to today.

Every scheduling rule works on the block-relative template: its order (the benchmark is the last
slot), its spacing, its deload weeks and the block-week numbering that week review and the
projection use. No backend logic read the stored session `day_of_week`.

## Decision

Keep block-relative scheduling exactly as it is, and stop calling a slot a weekday.

1. **The template stays block-relative.** `weekly_template[].day_of_week` (request, preview and
   stored block) is the slot, day 1-7 of the block week. Creation consumes it as before. Weekday
   values are never fed back into the template or the generator: sorting a Thursday-start week
   Thu/Sat/Mon as Mon/Thu/Sat would move the benchmark and change the order. A future weekday
   picker needs its own intent field; existing explicit templates keep this meaning.
2. **A session records three days, each with one meaning** (a060):
   - `block_day`: the slot it was generated from. Set at creation, never changed, not exposed.
   - `day_of_week`: the ISO weekday (1 = Monday) of its **plan date**,
     `COALESCE(original_scheduled_date, scheduled_date)`. A move does not change it.
   - `scheduled_date`: where the session is now. Calendar labels come from this.
3. **Preview labels are resolved at the response boundary** (P5b): each previewed slot gains the
   weekday its first occurrence falls on, computed from the request's start date. The resolved
   weekday never enters a stored template or a scheduling calculation.

Dates, categories, spacing, benchmark placement, deload flags and block-week numbering are
unchanged for every start weekday.

## Limitation

`original_scheduled_date` exists only since a003 (2026-06-20). A session moved before then lost
its plan date, so its `day_of_week` is the weekday of the date it was moved to. That is not a
verified plan weekday. Its `block_day` is still the slot it was generated from, because a060
copied the stored value instead of inferring it from dates.

## Consequences

- `GET /v1/planning/sessions` returns real weekdays in `day_of_week` for blocks that did not start
  on a Monday.
- "The first session is on the start date" holds only when the week's first slot is 1 (running's
  authored week starts at slot 2, so its first session is the day after the start).
- a060's downgrade copies `block_day` back into `day_of_week`, restoring every session's slot,
  including sessions created or moved after the upgrade. Stop the API before downgrading
  (`docs/DEPLOY.md`, P5).
