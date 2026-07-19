---
status: accepted
date: 2026-07-19
---
# Shadow-EKF head-correction replay

[AUD-C8](../audits/2026-07-18-integrity-audit.html) made the shadow EKF ([ADR-0041](0041-shadow-ekf-state-covariance.md))
idempotent per `(wellness observation, model version)`: an exact re-POST no longer
re-assimilates, and a **correction** (same observation, changed content) is detected and
marked `correction_requires_replay` — sticky — instead of being sequentially re-applied (an
EKF update is path-dependent; a corrected value cannot simply be layered on the old one). C8
left the flag **unconsumed**: nothing repaired the belief. This ADR consumes it.

## Decision — a bounded *head-correction* replay, not a full historical engine

Replay only when the corrected assimilation is still the **effective EKF head** for the
athlete: no later transition (predict / update / wellness / replay) exists after it. Then the
repair is exact and cheap — rebuild from the original predecessor belief and re-apply the single
corrected wellness update, appending a new belief row. A correction to a **mid-history**
observation stays pending (`blocked_mid_history`) and is owned by a **separate** deterministic
replay-engine mission. This is an *instrumented, bounded capability*, not complete correction
support: the slice repairs the tractable population and reports how much remains.

**Key decisions**

- **Lineage-aware head, per user.** The online chain's prior selection (`_load_latest_belief`)
  is per-user max-id, model-agnostic; replay eligibility reuses that exact head definition. After
  a replay, the head is the replay row of the same source/model, so a *later* correction on the
  same observation is still head-eligible — it supersedes the prior replay while rebuilding from
  the **original** predecessor (never assimilating on top of the prior replay).
- **`event_type` names the transition operator; source is orthogonal** (Q9). `predict` |
  `update` | `replay`; the wellness dimension is `source_wellness_sample_id`. Original-wellness
  uniqueness is scoped **positively** to `(source non-null AND event_type='update')` — a future
  source-carrying event type cannot silently inherit the contract — and replay idempotency to
  `(source non-null AND event_type='replay')` keyed by `correction_revision`. No event_type
  backfill.
- **Correction generations, not hashes.** Every changed `latest_seen_content_hash` increments a
  monotonic `correction_revision` (so A→B→A is two generations). Replay idempotency and the flag
  invariant key on the generation: `correction_requires_replay ⇔ replayed_revision <
  correction_revision`. A content hash alone is an insufficient replay identity.
- **Append-only, immutable history.** A completed replay **appends** an `event_type='replay'`
  row (`supersedes_log_id` → the head it replaced, `replay_base_log_id` → the predecessor belief
  it rebuilt from) and reconciles *mutable* metadata on the original row (`replayed_revision`,
  `replayed_at`, `replayed_by_log_id`, flag). The original numerical belief is never overwritten.
- **Shared per-user serialization.** Every EKF appender — predict, update, wellness, replay —
  acquires the same namespaced, transaction-scoped `pg_advisory_xact_lock(ns, user_id)` before
  any chain-dependent read or append. The unique indexes guard duplicate *identities*; the lock
  guards chain *order* (a replay must not append from a stale head while a concurrent workout
  advances it). A replay-only lock would not suffice — the ordinary appenders must participate.
- **Transaction shape.** A pre-existing eligible replay and the current assimilation run in **one**
  shadow transaction (so the current event assimilates on the corrected trajectory; an unexpected
  replay failure aborts before the current event can append and strand the correction mid-history).
  A **newly** detected correction commits first, then a **separate** best-effort transaction
  attempts its replay — so a replay failure cannot un-commit the durable pending correction.
  Reconciliation clears the flag only under compare-and-clear (`correction_revision` unchanged), so
  a newer generation is never falsely resolved. Everything stays `decision_impact=none_shadow_only`
  and never touches the live wellness write.
- **First-belief corrections stay pending.** If the corrected assimilation had no stored
  predecessor (it was the seed step), replay is blocked (`blocked_missing_predecessor`); current
  production state is **never** substituted for the historical seed (other paths may have advanced
  it). Deterministic first-belief replay requires persisting the original seed snapshot — a
  follow-up.

## Consequences

- Same-day corrections (the common case) self-heal on the ingest that detects them, and any
  transient failure is retried before the next assimilation makes the correction mid-history.
- Mid-history and first-belief corrections remain honestly pending, surfaced by structured
  `ekf_wellness_replay` logs (no metrics runtime exists yet; the outcome taxonomy maps 1:1 to
  future counters). Logs carry no soreness/HRV values, hashes, or belief payloads.
- Follow-ups: **EKF mid-history deterministic replay** (self-contained versioned transition
  envelopes) and **first-belief seed-snapshot replay**.

Schema: migration `a038_ekf_head_correction_replay`. Enforcement: `tests/test_ekf_head_replay.py`,
`tests/test_ekf_chain_lock.py`, `tests/test_ekf_replay_migration.py`.
