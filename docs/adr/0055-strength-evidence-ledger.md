---
status: accepted
date: 2026-07-09
---
# Training-derived e1RM is estimated lower-bound evidence, not benchmark measurement

P9 (PR #96) added write-time e1RM extraction: every loaded **top set** of a lift with an
`e1rm_benchmark_code` was written as a `benchmark_observation` with `validity="valid"`,
`source="workout_extraction"`, `observation_weight=1.0` — i.e. **indistinguishable from a
protocol-grade tested 1RM**. `apply_benchmark_observation` corrects capacity
**bidirectionally** toward the observation's normalized score, so this is **live state
corruption**, not just write amplification:

- **Easy training regresses the twin.** A submaximal `3×5 @ 70%` extrapolates to an e1RM
  *below* the athlete's real e1RM → the residual pulls `max_strength` **down**. Training
  easy makes the model think the athlete got weaker. That is backwards.
- **Honesty-ladder violation** (the spine of the redesign): an e1RM *extrapolated* from a
  backoff set is **estimated**, but it was written as a **valid measurement**.
- **Epley is unreliable past ~5 reps** (a 12-rep set extrapolates ~+40%), yet it still wrote.
- **Write amplification:** N lifts → N capacity observations → N state rows + N KPI
  recomputes + N EKF updates per session.

The invariant this violates (stated precisely — the distinction is **protocol-grade
measurement vs opportunistic workout extraction**, not "training vs non-training", because a
session may *contain* an intentional test):

> **Non-protocol workout logs may provide lower-bound evidence that the athlete is at least
> this strong** — they may raise a lower-bound strength floor or inform prescription basis.
> **They may not, by themselves, create negative capacity residuals or reduce `max_strength`.**
> Only **protocol-grade benchmark observations** may update capacity bidirectionally.

**Capacity estimate `X` and observed lower-bound evidence `L` are distinct.** Workout-extracted
e1RM updates **`L`** (a floor); it may **not** directly regress **`X`**. The model may use `L`
as a floor constraint but never as bidirectional measurement evidence. `L` series may fluctuate
historically; the authoritative floor is **monotonic** unless corrected by explicit
invalidation — so a *lower* later extraction never drags the floor (or `X`) down.

## Decision

**One canonical evidence ledger; `validity ≠ authority`.** We extend the existing
`benchmark_observations` table (do **not** create a parallel `movement_strength_evidence`
source of truth — that is the split-brain PDR-0003 forbids) with provenance + authority:
`source`, `evidence_type`, `observation_model`, `affects_capacity`, `can_regress_capacity`,
`effort_fidelity`, `exercise_id`, `workout_id`, and reps/load/rpe/rir/formula. High-watermark
and "current e1RM" become **derived queries**, not stored state.

**Three observation models, previously collapsed into one:**

| model | example | authority |
|---|---|---|
| `direct_capacity_measurement` | benchmark test / protocol single | `validity=valid_for_capacity`, weight 1.0, **may regress capacity** |
| `censored_lower_bound` | gated hard workout PR | `validity=valid_for_prescription`, low weight, **upward-only floor**, never regresses `X` |
| `training_estimate` | ordinary working set | `validity=tracking_only`, ~zero weight, **prescription/tracking/intensity only**, never touches capacity |

**`validity` is purpose-specific — "valid for *what*?"** — never a bare `valid`: `valid_for_capacity`
| `valid_for_prescription` | `tracking_only` | `quarantined` | `invalid`. Reusing an
undifferentiated `valid` is how the same bug returns with better column names.

**Extraction becomes gated, upward-only, estimated, and off the capacity path:**
- **Gate** — only informative sets extract capacity-relevant evidence: `reps ≤ 5` **and**
  high effort (`RPE ≥ 8` / `RIR ≤ 2`); stricter for `group_level` effort
  ([ADR-0045](0045-per-set-catalog-bound-workout-logging.md)): `RPE ≥ 9` / `RIR ≤ 1`, half
  weight. High-rep/low-effort sets write **tracking-only** evidence, never capacity.
- **Non-regressing ratchet** — an extracted e1RM ≤ current estimate is recorded for history
  (`affects_capacity=false`) but **must not** pull capacity down. Only a PR beyond a small
  deadband (~0.5–1%) records upward lower-bound evidence.
- **Never `_apply_capacity_residual`** for `source="workout_extraction"`. Capacity authority
  is narrow: only `benchmark_test` / `coach_verified` (and, later, explicitly protocol-grade
  singles) may correct capacity bidirectionally.

**Coverage: wide tracking, narrow authority, identity-based resolution.** Every e1RM-eligible
loaded movement may accrue an estimated series keyed by `exercise_id`, governed by a per-
`Exercise` `strength_estimation_policy` (`benchmark_authoritative` | `e1rm_tracking_only` |
`load_tracking_only` | `not_e1rm_applicable`) so sled pushes / carries / timed holds don't
pretend to have an e1RM. **All runtime e1RM resolution goes `exercise_id → e1rm_benchmark_code
→ ledger`** — exact-name matching (still live in prescription enrichment) is **retired**;
name matching is allowed only for one-time migration/admin repair.

**Remediation:** existing `workout_extraction` rows are backfilled to
`evidence_type=estimated_from_training_set`, `can_regress_capacity=false`, low weight; state
rows where a `workout_extraction` observation drove a **negative** `max_strength` residual are
quarantined/repaired (restore to `max(last benchmark estimate, prior high-watermark, current)`).

**This is the hotfix, and it lands FIRST** — before [ADR-0039](0039-dose-law-external-load-vs-effort.md),
because that ADR's intensity denominator (`load / e1RM_pre`) must read an uncorrupted e1RM.

## Consequences

- Adds a schema migration + a governed write path; `benchmark_observations` becomes a general
  evidence ledger (name may later generalize behind a compatibility view).
- `create_observation` gains authority gating; extraction stops calling the capacity path.
- Derived surfaces: `current_e1rm_for_prescription(exercise_id)` and
  `prelog_e1rm_for_dose(exercise_id)` (snapshot before extraction runs).
- Rejected: a parallel evidence table (split-brain); keeping bidirectional capacity updates
  from training (the corruption); all-time PR as eternal prescription truth (staleness).
