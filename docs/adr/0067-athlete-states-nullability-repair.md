---
status: proposed
date: 2026-07-17
---
# athlete_states nullability repair: fail-closed decoder, migration reuses the live projector

`athlete_states` has 8 columns (`timestamp`, the 4 legacy fatigue mirrors, `s_struct_signal`,
`habit_strength`, `skill_state`) the ORM declares non-`Optional` but the founding migration
created `nullable=True`. Investigation (not just the ORM/DB mismatch) found the *actual*
runtime exposure is narrower and different than it first looked: `skill_state`/
`s_struct_signal`/`habit_strength` are already coerced to a safe default by every
`UnifiedStateVector` construction path (`state_bridge.unified_from_athlete_row`,
`state_loading._passthrough_kwargs`) — a `NULL` there cannot reach a caller today. The two
*verified* crashes are `timestamp` (unguarded on every read path, since `UnifiedStateVector.timestamp`
is a required Pydantic field) and the four fatigue floats specifically inside
`state_bridge.fatigue_from_legacy`'s legacy-reconstruction branch (only reachable for rows
missing `engine_state` — `state_loading.py`'s newer `_legacy_recovery_view` already
pre-filters via `_finite`/`DisplayStateUnavailable` before calling it; `state_bridge.py`'s
older `unified_from_athlete_row` does not).

Three decisions, each hard to reverse and each a real trade-off:

1. **`fatigue_from_legacy`/`tissue_from_legacy` fail closed on a missing required scalar**
   (`IncompleteLegacyState`) instead of silently coercing to `0.0`. A shared bridge/decoder
   function must not manufacture "known zero fatigue" out of "missing fatigue evidence" —
   that's directionally unsafe on a prescription path. Zero-as-neutral-state stays confined to
   (a) the one-time migration backfill and (b) the explicitly-named, provenance-marked
   `reconstruct_legacy_state_for_display` read path — never the generic decoder every caller
   (including future ones) inherits.
2. **The migration backfill reuses the real Python projector** (`sync_legacy_from_vectors`,
   `FatigueState`/`TissueState` decoding) from inside the Alembic migration for rows with a
   valid `engine_state`, rather than reimplementing the formula
   (`f.structural + f.tendon + 0.15*f.grip + 0.1*tissue_avg`, etc.) in raw SQL. Two independent
   implementations of the same formula are a drift risk the moment either changes; importing
   and calling the live code guarantees agreement by construction, not by fixture-testing after
   the fact.
3. **`timestamp` backfill uses one migration-transaction UTC value**, not a per-row runtime
   fallback. `athlete_states` has no `created_at`/`updated_at` columns to recover a better
   historical value from (unlike `users`/`athlete_profiles`, which `a035`/AUD-C12 already
   tightened using exactly that kind of column) — this is genuinely a different table with a
   narrower repair option, not the same pattern replayed. The backfilled value is documented as
   schema-repair time, not reconstructed historical event time, and a runtime
   `row.timestamp or datetime.utcnow()`-style fallback is explicitly rejected: that would
   fabricate a new "freshness" value on every read instead of repairing it once.

Consequence: `state_bridge.py` picks up a small, additive, backward-compatible change
(`fatigue_from_legacy`/`tissue_from_legacy` signatures accept `float | None` and raise instead
of crashing incidentally) inside the file item `state-bridge-choke-point` already flags as an
active INT-05/INT-15 convergence point — this change does not touch either contested function
(`_migrate_engine_state`, `sync_legacy_from_vectors`'s own body), only the legacy-bootstrap
helpers, so it does not itself require the pending sequencing decision.
