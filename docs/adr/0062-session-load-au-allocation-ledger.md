---
status: proposed
date: 2026-07-11
---
# Session-RPE load (AU) is the microcycle allocation currency, distinct from state-update dose

[ADR-0050](0050-objectives-drive-training-emphasis.md) makes objectives compute a modality
*mix*, and [ADR-0060](0060-objective-mix-live-receding-horizon-microcycle.md) reconciles that mix
against completed and committed training over the fixed microcycle it defines.
That reconciliation — `remaining_need[m] = target[m] − completed[m] − committed[m]` — needs a
scalar unit that is **forward-plannable** (a not-yet-executed session has no logged sets, so no
[ADR-0037](0037-model-concurrent-interference.md) stress vector exists yet) and **modality-
comparable** (a long conditioning session and a short strength primer must not count as equal
merely because each occupies one slot). This ADR fixes that unit.

**The allocation currency is session-RPE load, in arbitrary units:**

```
session_load_au = effective_session_rpe × duration_minutes
```

This **reuses the existing `dashboard_service.daily_load` proxy** (already the ACWR load
signal) rather than inventing a second weighting system. It is an **internal-load proxy** — a
scheduling/allocation currency, *not* physiological state-update dose, mechanical work, or
benchmark evidence. The [ADR-0037](0037-model-concurrent-interference.md) stress/capacity
vectors remain the sole state-update currency; the AU ledger sits above them purely to answer
*how much training effort goes to each modality this week*. A higher session RPE can reflect a
harder session, poor sleep, heat, or accumulated fatigue, so AU is a shared allocation proxy,
**not** proportional adaptation stimulus — documented as a known limitation.

**The microcycle budget counts each contribution exactly once:**

```
B_H(t) = completed_actual_load_H + committed_estimated_load_H + uncommitted_feasible_capacity_H
```

Not "the original Monday plan" and not "only the remaining slots" — what has happened, plus
what is committed, plus what can still feasibly happen. Lost capacity (a training day that
disappears) shrinks `uncommitted_feasible_capacity`; the engine **never compresses** missed
load into the microcycle's final days, and unresolved deficits do **not** carry into the next
microcycle ([ADR-0060](0060-objective-mix-live-receding-horizon-microcycle.md)).

**RPE resolution is explicit — no silent duration-only fallback.** Treating a missing RPE as
duration alone is effectively `RPE=1`, which systematically undercounts RPE-missing sessions.
The allocation ledger resolves in order and stamps the semantics:

```
completed:  actual_session_rpe (reported_actual)
            → else target_rpe (planned_estimate)
            → else policy.expected_rpe (policy_imputed)     # versioned
            → else unavailable
planned:    target_rpe (planned_estimate) → else policy.expected_rpe (policy_imputed)
```

The legacy ACWR proxy may keep its duration-only compatibility fallback; that unit-changing
fallback **must not** enter the allocation ledger.

**Planned and actual are mutually exclusive in the active ledger.** A session contributes
once: an estimate while future, a derived-reported value after completion — the actual load
**supersedes** (does not accumulate with) its prior estimate. Both snapshots are persisted for
audit; only one is active. `planned 45min×RPE6 = 270 AU` then `completed 52min×RPE8 = 416 AU`
counts **416, not 686**.

**Completed session-RPE load is `derived_reported_actual`, not "measured."** It is derived
from self-reported exertion × recorded duration. It must **not** be labelled `measured` in the
[ADR-0058](0058-observation-provenance-capacity-authority.md) benchmark-authority sense —
validated benchmark measurement may update capacity bidirectionally; self-reported session
exertion is an internal-load observation used for workload accounting and fatigue context only.

**Modality attribution is fractional, and never circular:**

```
allocation_load[m] = session_load_au × modality_share[m]     Σ modality_share = 1
```

Single-modality sessions are one-hot (`{running: 1.0}`); mixed sessions use their **planned**
shares (`{strength: 0.65, conditioning: 0.35}`); a legacy/missing composition falls back to the
coarse derived session modality, **tagged** (`modality_attribution_source ∈
{explicit_planned_split, explicit_single_modality, derived_session_modality, legacy_coarse}`).
The split comes from the planned session/template — **never** from the objective target mix,
which would be circular. This is deliberately *not*
[ADR-0054](0054-per-exercise-dose-routing.md) physiological φ routing; it is coarse planning
attribution describing what the scheduled session was intended to service.

Every ledger contribution records: `duration_minutes/duration_semantics`,
`effective_rpe/rpe_semantics`, `session_load_au/load_semantics ∈
{derived_reported_actual, derived_planned_estimate, derived_policy_imputed}`, `modality_shares`,
`modality_attribution_source`, `load_by_modality`, `computed_at`, `allocation_policy_version`.

## Considered and rejected

- **Session count** — the pathology the whole horizon design avoids; one slot ≠ one dose-
  equivalent.
- **Pure duration (minutes) with a silent duration-only fallback** — ignores that a hard
  interval ≠ an easy jog per minute, and the fallback secretly means `RPE=1`.
- **Per-vector stress-dose magnitude ([ADR-0037](0037-model-concurrent-interference.md))** — the
  most physiological, but **not forward-plannable** (a future session has no logged sets) and
  multi-dimensional, so no single "share." That stays the state-update currency, not the
  allocation currency.
- **Forcing one-hot modality** — a mixed strength+conditioning session booked 100% strength
  makes conditioning look under-delivered and triggers a false catch-up. Fractional attribution
  is required.

**Guardrail:** `session_load_au = rpe × duration` is arbitrary-unit **allocation** currency
only — never conflate it with [ADR-0037](0037-model-concurrent-interference.md) state-update
dose or [ADR-0058](0058-observation-provenance-capacity-authority.md) benchmark authority. Each
session revision contributes to `B_H` exactly once (estimate superseded by actual on
completion), RPE never silently collapses to duration-only, and modality attribution is never
derived from the objective mix.
