# Shadow-cascade diagnostic — bounded reproduction & classification

**Date:** 2026-07-18 · **Origin:** surfaced while building AUD-C22 (wellness-route durability test).
**Status:** classified — production-real. Fix is a **separate candidate** (no production change made here).

## Question

The `/v1/wellness` route runs three best-effort shadow writers after the durable upsert, on
one shared session, each handed the same live `sample` ORM instance. When an early shadow
rolls back (its `best_effort_write` failure path), does that rollback invalidate ORM state a
*later* shadow still expects to read? Or is the failure seen while building C22 only a
NullPool test-fixture artifact?

## Method — bounded matrix

A minimal reproduction (commit a row, early "shadow" does DB work then `rollback()` which
expires the instance, later "shadow" reads it) across the decisive axes:

| pool | later access | result |
|------|--------------|--------|
| `NullPool` | attribute on the expired **live instance** | **RAISED `MissingGreenlet`** |
| `NullPool` | **reload by identity** (`session.get`) | OK |
| pooled (prod-like) | attribute on the expired **live instance** | **RAISED `MissingGreenlet`** |
| pooled (prod-like) | **reload by identity** | OK |

## Classification — production-real session-coupling defect

The failure reproduces on a **pooled engine** (production-like), so it is **not** a
NullPool/test artifact. The decisive dimension is **data-handoff, not the pool**:

- Reading an **expired live ORM instance** after an earlier shadow's rollback triggers an
  async *implicit lazy-load*, which raises `MissingGreenlet` on both pools.
- **Reloading by identity** (an explicit awaited SELECT) is safe on both pools.

In production the wellness route shares one session across the three shadows and hands each
the same live `sample`. So an early shadow's rollback expires `sample`, and each later shadow
that reads it hits `MissingGreenlet` — caught and swallowed by `best_effort_write` (no 500),
but its telemetry row is **silently not written**. Impact: one early shadow failure drops all
later shadows' telemetry for that request (data loss in the shadow logs), not a crash.

## Invariant to hold

> One shadow's rollback, expiration, or persistence failure must not alter the inputs or
> execution of a later independent shadow.

## Recommended remedies (for the separate fix candidate)

1. **Immutable payload hand-off** *(preferred, smallest):* snapshot `sample` into a plain
   immutable payload (dataclass/dict) *before* the shadows run, and pass that instead of the
   live ORM instance. The route already materializes `result` before the shadows for the
   response; this extends the same discipline to the shadow inputs.
2. **Reload by identity:** each shadow re-fetches what it needs via `session.get(...)` inside
   its own `best_effort_write` (safe on both pools).
3. **Per-shadow session:** each best-effort shadow owns its own session/transaction, so
   isolation does not depend on shared-session state.

## Disposition

Open as a new ledger candidate (**AUD-C24: shadow-input isolation**). Per the grill lock, no
production change is made until classification (done); the fix is scoped separately from the
#165 audit-remnants batch and from AUD-C8 (EKF idempotency), though remedy (1) would also
harden the EKF path.
