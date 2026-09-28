---
status: proposed
date: 2026-09-28
---
# Planned-week projection is display-only fatigue, not a readiness forecast

Resolves the C0 -> C1b question for the console Planning screen. Relates to
[ADR-0064](0064-receding-horizon-modality-assignment.md) (no invented future-readiness forecast),
[PDR-0005](../pdr/0005-one-backend-owned-readiness-number.md) (one backend-owned readiness number),
[ADR-0060](0060-objective-mix-live-receding-horizon-microcycle.md) (block week = `block_id + week_number`) and
[ADR-0069](0069-date-move-does-not-change-session-status.md) (a move changes the date).

## Context

The Planning screen wants a forward view of the week: what the planned sessions will do to
load and fatigue. The existing forward projection (`POST /v1/simulate/projection`,
`app/services/projection_service.py`) synthesizes a *hypothetical* plan from a goal and a
volume slider; it does not read the athlete's planned sessions.

Two things constrain what such a projection may claim:

* Planned sessions carry no dose. Their content is resolved on the day by the prescriber; a
  session that has not been opened has only a slot (domain, deload/benchmark flags).
* Readiness is one backend-owned scalar that combines modeled fatigue with acute wellness
  (PDR-0005). Future wellness is unknown, so a "future readiness" curve would be invented —
  exactly what ADR-0064 rules out.

## Decision

`GET /v1/planning/projection?through=YYYY-MM-DD` projects the athlete's **PENDING** planned
sessions from today through `through` (default: end of the current block week; no active
block -> today+6; capped at 28 days) through the real engine, one day at a time, and reports
per day: the sessions, their sRPE load, end-of-day **mean fatigue** and the six fatigue axes.

1. **Display-only.** It writes nothing and is never an input to prescription, scoring or any
   gate. Its unavailability is `200 {available: false, reason}` (`no_state`,
   `state_invalid`), not a 409: no capability is refused.
2. **Fatigue, not readiness.** No readiness series is returned. The mean-fatigue series is
   modeled fatigue and is labelled as such.
3. **Real engine, estimated sessions.** Each pending session becomes a synthetic
   `WorkoutLog` (`app/logic/planned_session_log.py`): modality from its domain (block goal
   when absent), duration from the block's target minutes (else the modality baseline) x the
   deload factor on a deload session, intensity from the stored prescription's max RPE cap
   (balanced when none). Every session says whether that intensity was `prescribed` or a
   `template_estimate`. Doses go through the pinned production `app.logic.dose_engine`.
4. **Membership by date.** Sessions belong to the day they are scheduled on now (ADR-0069).
   Completed sessions are not re-projected; they are already in the state.
5. **Strict start state.** `load_current_state_strict`; a damaged state makes the projection
   unavailable rather than being reconstructed from legacy scalars.
6. **Load is sRPE.** `dashboard_service.daily_load` — the same proxy ACWR uses, so Planning,
   History and Overview agree.

## Consequences

* The Planning screen can chart load vs modeled fatigue for the real plan. The mock's
  "load vs readiness" chart becomes "load vs modeled fatigue".
* The numbers for un-opened sessions are template estimates and will move once a session is
  prescribed; the `basis` field is what lets the UI say so.
* Duration is not read from a stored prescription — a prescribed session's length may differ
  from the projected one.
* If a readiness-shaped series is ever wanted, it must be a separately typed field
  (e.g. `modeled_readiness{kind: "fatigue_proxy"}`) and a new decision, not a rename of this one.
