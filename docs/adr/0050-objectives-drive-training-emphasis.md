---
status: proposed
date: 2026-07-07
---
# Objectives drive concurrent training emphasis

The `Objective` model is fully built (multi, concurrent, cross-domain: benchmark + target +
date + priority), but it barely reached the prescriber: the day's content came from a
*single* resolved goal (block → query → `primary_goal` → default), and all active objectives
together contributed only a taper and one `+0.15` domain boost. [ADR-0030](0030-block-derived-intent-modality-mix.md)
explicitly deferred the real integration — `modality_mix` stays authoritative "until
Objectives supersede it" — and that supersession was never built. So multi-goal was
cosmetic: the UI showed many objectives while the planner trained one. That is worse than
not supporting objectives at all.

Active objectives now **compute the training emphasis.** Each contributes a weighted modality
vector by `priority × proximity-to-date × gap-to-target × status`; the weighted, **smoothed**
(hysteresis, so the plan doesn't thrash week to week) blend becomes the block-level
`modality_mix` that drives **multi-domain candidate generation** — running *and* strength
*and* support candidates scored against the blended state, not one goal's workout. A 5k
objective (priority 1, near) and a squat-total objective (priority 2, far) yield a run-leaning
blend that shifts to strength automatically as the 5k passes and the squat date nears — no
manual mode switch. Before candidate generation the mix passes through **safety constraints,
phase logic, and minimum-effective-dose floors** (so a secondary objective is never fully
neglected). `primary_goal`/`block_goal` demote to a fallback used only when no structured
objectives exist. We rejected keeping objectives as nudges (product dishonesty).

This realizes [PDR-0004](../pdr/0004-objectives-first-class.md) and completes the
[ADR-0030](0030-block-derived-intent-modality-mix.md) supersession. What a user with several
objectives is really asking is not "what is my one goal?" but "how should my limited capacity
be allocated across competing objectives over time?" — which is exactly what `modality_mix`
represents.

**Guardrail:** when structured objectives exist, they compute the `modality_mix` the
prescriber pursues — never fall back to a single primary goal, and never let both compete as
sources of truth. Objectives are prescription-driving control inputs, not labels.

---

## Amendment — 2026-07-11: decomposed by design grill

A grill-with-docs session took this ADR from accepted-*shape* to a decision-complete spec.
The direction above stands; the *how* is now recorded across six sibling ADRs, and this one
is the umbrella that names the spine. Two framing corrections came out of the grill:

- **"Shifts automatically" is a runtime property, not a stored value.** The mix is recomputed
  live at every prescription boundary; the block's mix is an immutable creation snapshot, not
  runtime authority. See **[ADR-0060](0060-objective-mix-live-receding-horizon-microcycle.md)**.
- **A mix is a horizon *allocation*, not a per-session instruction.** Objective emphasis updates
  immediately at the intent layer; concrete training adapts only at the next safe, uncommitted
  opportunity, reconciled against a dose ledger. Past sessions are never retroactively "caught up."

The six sub-records:

- **[ADR-0060](0060-objective-mix-live-receding-horizon-microcycle.md)** — live per-request
  recompute; three representations (creation snapshot / live target / effective-remaining);
  fixed block-local microcycle as the accounting horizon; deficits expire at its boundary.
- **[ADR-0061](0061-objective-target-share-function.md)** — the target-share function itself:
  multiplicative `priority × proximity × gap`, confidence-aware gap, diminishing same-domain
  aggregation, true post-normalization share floors, objective lifecycle + explicit maintenance,
  anchor-as-priority-1, and event-keyed taper.
- **[ADR-0062](0062-session-load-au-allocation-ledger.md)** — the allocation currency:
  `session_load_au = rpe × duration` (internal-load AU), distinct from ADR-0037 state-update dose.
- **[ADR-0063](0063-session-commitment-and-issuance.md)** — the commitment/issuance state machine
  (adaptivity-first, issue ≠ materialize, scoped commitment, safety invalidates-not-rewrites).
- **[ADR-0064](0064-receding-horizon-modality-assignment.md)** — hierarchical receding-horizon
  slot→modality assignment, the typed `ResolvedPlanningConstraint` / `PlanningDecisionTrace` seam,
  and the P11/P12 boundary (this ADR stops where [ADR-0051](0051-user-owns-structure-engine-owns-safety.md)
  begins).
- **[ADR-0065](0065-objective-progress-signal-evidence-contract.md)** — `ObjectiveProgressSignal`
  as a derived projection and the P10→P11 evidence contract (P11 runtime gates on P10; schema and
  logic proceed on fixtures in parallel).

**Sequencing:** P10 ([ADR-0057](0057-domaincode-three-roles-one-vocabulary.md)/[ADR-0058](0058-observation-provenance-capacity-authority.md)/[ADR-0059](0059-seed-uncertainty-and-provisionality-views.md))
ships the evidence provenance P11's confidence-aware gap consumes; only P11's runtime
progress-evidence adapter blocks on it.
