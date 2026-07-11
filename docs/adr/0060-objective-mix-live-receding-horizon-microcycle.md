---
status: proposed
date: 2026-07-11
---
# The objective mix is recomputed live per request; the microcycle is the accounting horizon

[ADR-0050](0050-objectives-drive-training-emphasis.md) decided that active objectives *compute*
`modality_mix`, but left *when* unresolved. Today the mix is computed **once at block creation**,
frozen into a JSONB column on the block, and immediately materialized into `PlannedSession` rows —
so ADR-0050's promise ("shifts to strength automatically as the 5k passes") cannot hold: a value
frozen at creation cannot shift mid-block. This ADR fixes the recompute timing and the accounting
horizon; the weighting function is [ADR-0061](0061-objective-target-share-function.md), the dose
ledger is [ADR-0062](0062-session-load-au-allocation-ledger.md), and the slot assignment is
[ADR-0064](0064-receding-horizon-modality-assignment.md).

**The target mix is recomputed live at every prescription boundary** (`/today`, `/next-session`,
plan regeneration, session replacement, objective mutation/completion) — request-driven, **not** a
weekly job and **not** block-creation-only. Recomputation is **idempotent by input fingerprint**
(objective-state version, as-of date, athlete-state version, completed-session version,
schedule/availability version, policy version): unchanged inputs return the same prescription
revision, so repeated page refreshes never regenerate a session. "The target changed immediately"
does **not** imply "the next session must change immediately" — the next session changes only when it
is still uncommitted and the change is feasible (see [ADR-0063](0063-session-commitment-and-issuance.md)).

**Three representations, kept distinct** — collapsing them is the failure mode ADR-0030's single
authoritative `modality_mix` invited:
- `block.initial_modality_mix` — the objective-derived mix **as computed when the block was
  created**. Immutable provenance/display history; **never read as current prescription authority**.
  (The ambiguous `block.modality_mix` name is retired precisely so a stale snapshot can't be
  mistaken for canonical. This supersedes ADR-0030's "`modality_mix` stays authoritative" framing.)
- `target_modality_mix(as_of)` — a pure, versioned calculation from active objectives / priority /
  target dates / progress-gaps / status / policy version. Recomputed when the input fingerprint
  changes, reused otherwise.
- `effective_remaining_mix` — the target reconciled against reality (completed dose, committed
  future sessions, remaining feasible budget, safety/recovery). **The session generator consumes
  this** — not the raw target and not the block snapshot.

**Three horizons that must never be collapsed into one:**
- **Objective horizon** — today → each objective's target date. Informs the *weighting* only; a 5k
  three weeks out can raise this week's running share without a three-week forward dose model.
- **Allocation / accounting horizon** — the **current block-local microcycle**, normally one
  scheduled week, identified by `block_id + week_number` with explicit start/end **dates** (not the
  `week_number` label), so shortened first/final weeks, deloads, and travel weeks are handled by
  membership dates. **Not a rolling seven-day window** — a moving denominator makes the same
  completed/locked sessions repeatedly enter and leave the bucket, manufacturing mix movement with
  no change in objectives (the scheduling nervousness the commitment design exists to prevent).
- **Safety / lookahead horizon** — enough future days to enforce spacing and recovery.

**Microcycle deficits expire at the boundary.** Missed or infeasible dose is **never compressed
into the final days** of the microcycle, and unresolved modality deficits do **not** automatically
carry into the next one (a controlled multi-week carryover is a future optimizer's job, not a hidden
accumulator here). At each boundary the engine recomputes objective weighting, establishes a fresh
feasible budget, and starts completed/committed accounting at zero. Historical training still affects
athlete state, fatigue, and objective progress — it simply is not a standing modality-mix liability.

Rejected: a **rolling seven-day** denominator (overlapping accounting periods → artificial mix
churn); **weekly recomputation** (objective completion stays stale for days, arbitrary calendar
boundaries, needs a scheduler we deliberately dropped with the EC2 move); **block-creation-only** (a
3–6-week delay after an objective completes is static periodization, not the adaptive prescription
ADR-0050 requires).

**Guardrail:** the block snapshot is immutable provenance and is never consumed as live mix
authority; live recomputation is request-driven and idempotent by input fingerprint; the objective
horizon may look to each target date, but dose reconciliation is bounded to one fixed block-local
microcycle whose deficits expire at its boundary.
