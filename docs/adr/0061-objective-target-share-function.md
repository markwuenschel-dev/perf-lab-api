---
status: proposed
date: 2026-07-11
---
# The objective target-share function: multiplicative score, diminishing aggregation, true share floors

[ADR-0050](0050-objectives-drive-training-emphasis.md) decided *that* active objectives
compute the training emphasis; this ADR fixes *how* — the pure function `target_modality_mix(as_of)`
that turns the live objective set into the modality shares `p_m(t)` consumed by the
[ADR-0060](0060-objective-mix-live-receding-horizon-microcycle.md) horizon planner. Today the whole signal is
a single `{taper, domain}` boost off the top-priority (or macrocycle-anchor) objective — cosmetic
multi-goal. The shape below replaces it. Constants live in a versioned `objective_mix_policy`; the
*shape* is the decision, the *constants* are tunable.

**Per-objective score is multiplicative: `w = priority × proximity × gap`.** Each active *pursuit*
objective contributes weight to its `domain`. Multiplicative (not additive) so a far-off, already-met,
low-priority objective correctly nearly vanishes rather than staying loud.

```
priority_factor  = 1 / priority_rank          # P1→1.0, P2→0.5, P3→0.33; versioned map,
                                              #   tunable to e.g. P1=1.0/P2=0.4/P3=0.2 if harmonic is too weak
proximity_factor = 1 / (1 + days_to_go / τ)   # τ ≈ 42d; days_to_go = max(0, target_date - today)
                                              #   → an overdue objective caps at 1.0, never explodes
                                              #   → no target_date: versioned no_date_proximity (steady, never urgent)
                                              #   → also emit objective_overdue when days_to_go was clamped
gap_factor       = reliability · observed_gap + (1 − reliability) · neutral_gap   # neutral_gap = 0.5
                                              #   observed_gap = clamp(0, 1, 1 − progress_pct/100)
```

**The gap is confidence-aware, not raw progress.** A precise-*looking* estimate must not receive
measured authority. `reliability` is **policy-derived** from the progress evidence
(`measured → 1.0`, `estimated → β_estimated`, `unknown → 0.0`; `β_estimated` versioned,
provisionally calibrated, caller cannot choose it) — the descriptor comes from
[ADR-0065](0065-objective-progress-signal-evidence-contract.md), not from the objective row. Unknown progress therefore
pulls toward the neutral 0.5; estimated is partially trusted; only authoritative measurement gets the
full gap response.

**Same-domain objectives aggregate with diminishing returns and a cap — objective count must not
manufacture authority.** Linear summation (`raw_m = Σ w_i`) lets three granular P3 running objectives
out-vote one broad P1 strength objective. Instead:

```
weights_m       = descending objective weights for domain m
domain_score[m] = min(score_cap, weights_m[0] + ρ_same_domain · Σ weights_m[1:])   # ρ < 1, cap = 1.0
```

A second objective in a domain reinforces the signal; it does not count as a fully independent demand.

**The minimum is a *share* floor, applied after normalization — not a raw-score floor.**
`raw_m = max(raw_m, 0.15)` is not a 15% dose floor once another domain scores 2.0 (it becomes ~7%).
Reserve the floor as normalized share:

```
require Σ floor[m] < 1                                   # else InvalidObjectiveMixPolicy
q_m           = domain_score[m] / Σ domain_score
desired_share = floor[m] + (1 − Σ floor) · q_m           # guarantees the intended minimum
```

The [ADR-0060](0060-objective-mix-live-receding-horizon-microcycle.md) bounded-transition smoother operates on
the residual **after** floors are reserved, so a newly-active domain still reaches its promised floor.
If active domains can exceed the floor budget, cap active domains or scale floors *deterministically*
and emit the policy decision — never over-allocate and silently renormalize the guarantee away.

**Reaching target does not create maintenance.** Lifecycle is `active pursuit → completion_candidate
→ completed`: a completion-candidate's gap contribution falls to zero while its active-domain floor
holds temporarily; a completed objective contributes zero and its floor is removed. `progress ≥ 100`
**never** implies perpetual maintenance — a met objective is completed or **explicitly** transitioned
to a separate `maintenance` mode (its own small floor, no deadline urgency, no gap multiplication).
"Secondary objectives are never fully neglected" applies to *active* objectives, not every objective
ever achieved.

**The macrocycle anchor gets no privilege — its influence is its priority.** Anchor *exclusivity*
(the `signals_from_anchor` path that let one objective suppress the rest) is retired; all active
objectives blend. The anchor receives **no multiplier and no protected share** — it is normally
priority-1, so it already leads through the one mechanism above. The anchor stays immutable
planning lineage / display context ([ADR-0040](0040-macrocycle-thin-container.md)); completing it
removes its live contribution but never rewrites macrocycle provenance. If a P1 objective proves too
weak against several lower-priority domains, revise the versioned `priority_factor` map — do **not**
add a hidden anchor boost (a second, partially-hidden priority system that double-counts intent).

**Taper is decoupled from the mix and keyed to explicit events.** Objective share (`p_m`) and total
load (`B_H`) are different axes; a `target_date` alone is not a competition. Taper eligibility comes
from a typed event (`taper_eligible = active_event ∧ taper_profile_code ≠ null`), not from any dated
objective — a body-composition deadline or coach review must not deload training. The controlling
event is selected by **importance → proximity → stable id**, and taper modifies the microcycle's total
feasible budget `B_H` ([ADR-0060](0060-objective-mix-live-receding-horizon-microcycle.md)), **not** the normalized
`p_m`. `signals_from_anchor` / `signals_from_scan` are replaced by three explicit resolvers:
`resolve_objective_target_mix` (this ADR), `resolve_taper_signal` (event-driven), and
`resolve_macrocycle_context` (display/lineage only, no prescription weight).

Rejected: **linear same-domain summation** (objective-count inflation); **raw-score floors**
(not an actual share guarantee); **implicit `progress ≥ 100` maintenance** (ghost objectives lingering
via a hardcoded `gap=0.2`); **an anchor amplifier / protected domain share** (double-counts priority
through the macrocycle pointer); **taper on any dated objective** (deloads for non-events). Domain is
keyed on the canonical taxonomy ([ADR-0038](0038-canonical-domain-taxonomy.md)); domainless objectives
take no modality share (they may still countdown and, via an event, taper).

**Guardrail:** objective emphasis is one multiplicative score (priority × proximity × confidence-aware
gap), aggregated with diminishing same-domain returns, floored as a post-normalization *share*.
Objective count, a precise-looking estimate, a reached target, and the macrocycle anchor may none of
them manufacture modality authority — priority is the only weighting mechanism, and only an explicit
taper-eligible event may reduce total load.
