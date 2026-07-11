---
status: proposed
date: 2026-07-11
---
# Session commitment is execution state; issuance is a user-visible event, not materialization

The receding-horizon prescription loop ([ADR-0064](0064-receding-horizon-modality-assignment.md))
re-solves the microcycle on every `/today` / `/next-session` and adapts modality as objectives
move. That only stays honest if some sessions are **frozen** from replanning — the ledger
([ADR-0062](0062-session-load-au-allocation-ledger.md)) subtracts `committed_estimated_load`, and
"issue only the next slot, leave the rest soft" ([ADR-0064](0064-receding-horizon-modality-assignment.md))
needs a definition of *soft*. This ADR defines what commits a session, and the state it moves
through. It is **P11-owned execution state**, distinct from the durable structural authority of
[ADR-0051](0051-user-owns-structure-engine-owns-safety.md) (P12): commitment applies to one
issued/accepted/started session revision; a `PlanningOverride` is durable block/phase intent.

**Adaptivity-first — no default freeze.** There is **no** default multi-day rolling commitment
horizon. Future planned sessions are **soft scheduling scaffolds** (day/time/duration/equipment,
per [ADR-0011](0011-lazy-planned-session-content.md)) whose modality/exercises/estimated-load
stay replannable until an explicit commitment event. A rolling 48–72h freeze is **rejected**: it
reintroduces an arbitrary policy boundary, dilutes live objective recomputation, and still needs
explicit acceptance + safety invalidation + revision history — so it buys nothing the scaffold
doesn't already give (the scaffold shows day/time/duration while modality stays fluid). A future
`auto_commit_horizon_days = N` preference may *auto-create explicit commitments* within a
horizon, but must never be a parallel hidden freeze.

**State machine (no direct jumps).**

```
soft_scaffold → materialized_preview → issued → accepted → started → completed
                                     ↘ accepted ↗
terminal: skipped | expired | superseded | safety_invalidated
```

- `soft_scaffold` — scheduling constraints only; modality/content replannable.
- `materialized_preview` — a concrete future session generated for display/planning, **explicitly
  labeled "may adapt before training day." Materialization alone confers no commitment.**
- `issued` — a specific prescription revision delivered as *today's* actionable session.
- `accepted` — athlete/authorized-coach explicitly accepted or pinned (can commit a future day).
- `started` / `completed` — in progress / historical fact; never structurally replanned.

**Issuance ≠ materialization/prefetch.** A session commits only when **user-visible delivery
succeeds** or the athlete explicitly accepts it — **not** when `GET /today` was called, a
background worker warmed a cache, or a client prefetched. Equating an endpoint hit with "the
athlete saw and relied on the session" is **rejected**. Issuance is a distinct service operation
(even if the REST route stays `/today`) persisting `issued_revision_id`, `issued_at`,
`delivery_surface`, `delivery_receipt_at`, `athlete_local_date`, and is **transactionally
idempotent**: one active issued revision per scheduled occurrence; repeated calls under unchanged
inputs — or ordinary input drift — return the same revision. A clean shape is `GET
/v1/prescriptions/today` (retrieve if issued) + `POST /v1/prescriptions/today/issue` (idempotent
issue); the route may vary, the semantic split may not.

**Commitment has scope** — not a generic `pinned=true` (**rejected**: "keep Wednesday 18:00" is
not "freeze Wednesday as a run"):

```
schedule     → date / time / duration / location   (modality stays soft)
modality     → modality allocation
prescription → modality + exercises + dose envelope
full         → complete revision (coach competition-prep, with actor provenance)
```

Default: today-issuance ⇒ `prescription` scope; future scaffold ⇒ `schedule` only; explicit
"accept this session" ⇒ `prescription` scope. Persist `commitment_source` (`athlete_acceptance |
athlete_pin | coach_fix | today_issuance | session_started`), `committed_by_actor_id`,
`committed_at`, `commitment_policy_version`.

**Never silently rewritten.** Ordinary objective / progress / readiness / optimizer movement
does **not** alter an issued or accepted revision — it applies at the next *uncommitted*
opportunity. An athlete-requested "adapt / reschedule / replace" creates a **superseding
revision**; the issued revision is **never mutated in place** (**rejected**), preserving history.

**Safety may invalidate, never silently rewrite.** A new contraindication may invalidate a
committed session, but must: preserve the revision, mark `safety_invalidated`, record blocking
reason + policy version, remove its `committed_estimated_load` from the ledger, and visibly
cancel/replace. *Safety owns the veto; P11 owns the scheduling consequence.* For a `started`
session, provide stop/abort/guidance per policy — do not swap in a structurally different workout
mid-session.

**Ledger + expiry.** Only `prescription`-committed revisions feed `committed_estimated_load_H`;
`soft_scaffold` / `materialized_preview` / `schedule`-only slots remain
`uncommitted_feasible_capacity_H`. On completion, actual AU supersedes (does not add to) the
estimate. At scheduled-day expiry the revision is marked `expired`/`missed` and **never returns
to the soft planning pool** — the next cycle decides what follows (no rewriting yesterday).

**Guardrail:** silent structural changes to an active issued/accepted prescription = 0. Commitment
is created only by user-visible issuance, explicit acceptance/pin, coach fixation, or start —
never by backend materialization or prefetch. Safety may invalidate a commitment (visibly,
audited) but may not rewrite it; ordinary optimizer/objective movement acts only on uncommitted
future slots.
