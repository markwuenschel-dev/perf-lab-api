---
status: proposed
date: 2026-07-11
---
# Objective mix reaches the plan through hierarchical receding-horizon assignment

[ADR-0060](0060-objective-mix-live-receding-horizon-microcycle.md) makes the objective target mix a live, receding-horizon
quantity and [ADR-0062](0062-session-load-au-allocation-ledger.md) reduces it to `remaining_need_H[m]`
(dose deficits over the current microcycle). Neither says how a *share vector* becomes the athlete's
next concrete session. Today it can't: `recommend_next_session` resolves **one** domain
(`_candidate_domain(goal)`), generates templates for only that domain, and adds a `+0.15` boost when a
candidate's domain matches the single objective domain — the mix never reaches candidate generation and
multi-domain never happens. This ADR defines the selection layer and, with it, the P11/P12 seam.

## Hierarchical, feasibility-aware, receding-horizon

Selection is **two layers, kept separate**: an upper layer decides *which modality* each remaining soft
microcycle slot should serve; the existing `get_templates` / `score_template` engine decides *which
concrete session* realizes that assignment (behaviorally unchanged). Aggregate allocation and detailed
executable scheduling stay distinct, and only the next action is committed — later slots are re-solved
as observations arrive.

**Stage 1 — proposals (pure).** For every remaining soft slot `s` and supported modality `m`, a
side-effect-free `preview_best_session(slot, modality, state, readiness, equipment, restrictions)`
returns either `infeasible(reason_codes)` or a proposal carrying `candidate_template_id`,
`estimated_load_au`, an estimated `load_by_modality` vector (fractional — a mixed session is not one-hot;
[ADR-0062](0062-session-load-au-allocation-ledger.md)), and constraint metadata. It reuses the concrete
engine but **issues nothing** and **must not compare template-quality scores across modalities** — that
comparison is what would collapse the hierarchy.

**Stage 2 — assignment.** Choose at most one feasible proposal per remaining soft slot to minimize
projected shortfall:

```
projected_residual[m] = max(0, remaining_need[m] − Σ assigned_load_by_modality[s,m])
loss = Σ_m importance[m]·normalized_residual[m]²
       + overfill_penalty + spacing_penalty + recovery_risk_penalty + preview_churn_penalty
```

The squared residual discourages starving one objective to make tiny gains elsewhere. Hard constraints
(availability, duration, equipment/location, safety restrictions, recovery spacing, max modality
frequency, committed-session boundaries) stay hard. **Slack is legitimate:** a slot may be left
unassigned when no safe candidate exists, recovery forbids training, demand is already met, or data is
insufficient — `unassigned ≠ solver failure`, it carries a reason. Future readiness is treated
conservatively: full concrete scoring only for the next issuable slot; later slots use hard spacing and
recovery constraints, never an invented future-readiness forecast.

**Issue only the next actionable proposal.** The rest of the assignment stays soft/advisory and is
re-solved after the next observation — completed actual load supersedes its estimate, readiness or an
objective changes, a session is skipped, a future session is accepted/pinned, or a slot disappears. This
is genuine receding-horizon behavior: solve the short horizon, execute the first action, observe, solve
again. The horizon is small (typically 1–6 slots) with the candidate set pruned to one best proposal per
slot-modality pair, so a **proportional** deterministic method (bounded enumeration / branch-and-bound /
DP / small beam) suffices — no heavyweight solver. Given identical objective state, remaining need, soft
slots, committed sessions, athlete state, readiness, equipment, and policy versions, the assignment and
issued candidate are **identical**, with explicit tie-breaking (lower loss → lower safety/recovery cost →
lower churn → canonical modality order → stable template id); never DB iteration order.

`_candidate_domain(goal)` and the `+0.15` boost are removed from final authority: the modality is chosen
by assignment, and **objective intent is not counted again inside template scoring** (that would double-
count — once in assignment, once in the boost). Within-domain scoring may still distinguish templates that
serve different objective details, but it never reselects the modality.

**Degraded fallback, never silent.** One-step greedy (largest normalized feasible current deficit, then
the existing per-domain scorer) exists **only** as an observable fallback — `planning_mode =
greedy_fallback`, `fallback_reason ∈ {solver_timeout, invalid_horizon, internal_error}` — and is never
presented as horizon-optimized. If no safe candidate exists, return **no prescription**; never bypass a
safety constraint to fill the calendar.

## The P11/P12 boundary

P11 owns the whole adaptive loop — blend → ledger → assignment → issuance → commitment
([ADR-0063](0063-session-commitment-and-issuance.md)) — plus a **minimal executable precedence** it needs to
resolve its own conflicts today:

```
safety  >  committed session revision  >  fixed schedule
        >  objective mix / floors  >  horizon optimizer  >  within-domain scorer
```

This is not yet [ADR-0051](0051-user-owns-structure-engine-owns-safety.md)'s full authority engine — it
is the smallest ordering that makes P11 coherent. P11 **consumes** existing safety/readiness decisions
and invents no new safety policy; a safety veto invalidates a committed session (preserve the revision,
mark `safety_invalidated`, drop its committed AU, offer a safe replacement) rather than silently
rewriting it.

Two typed seams make P12 an insertion rather than a rewrite:

- **`ResolvedPlanningConstraint`** — the solver's constraint input is a typed, scoped, provenance-bearing
  record (`kind`, `hardness`, `authority_class ∈ {safety, commitment, schedule, user_override,
  objective_policy}`, `scope ∈ {session_revision, slot, microcycle, block, phase}`, `effective_from/until`,
  `source_type/id`, `actor_id`, `target`, `reason_code`, `policy_version`), **never** an untyped dict.
  P11 produces constraints from safety, commitment, schedule/availability, equipment, and objective share
  floors. P12 compiles `PlanningOverride` rows into more `ResolvedPlanningConstraint`s and hands them to
  the **unchanged** solver — **P11 never reads `PlanningOverride` directly**, keeping persistence and
  authority policy out of the scheduling core.
- **`PlanningDecisionTrace`** — the explanation channel is machine-readable (objective shares considered,
  remaining need by modality, completed/committed AU, constraints applied, excluded proposals, assigned
  modality + candidate, projected shortfall, unassigned slots, solver mode, fallback reason, policy
  versions), **never** a free-form string. P11 may state current-cycle AU shortfall ("this pin would leave
  ~600 AU of strength demand unallocated this microcycle"); it must **not** manufacture objective-date
  costs ("adds three weeks to your squat") — that requires forward projection + uncertainty and belongs to
  P12's tradeoff estimator and the Simulator.

Conflict resolution is **scope- and time-aware, not a single global numeric ladder**: a durable override
normally affects future soft sessions and does **not** silently rewrite an already-issued session unless
the athlete explicitly requests that supersession. The seam is proven by a closure test — a synthetic
external hard constraint can be injected and honored **without changing solver architecture**.

## Considered and rejected

- **One-step greedy as the primary path** — myopic; spends today on the biggest deficit and discovers the
  last remaining slot can't cover what's left ([ADR-0062](0062-session-load-au-allocation-ledger.md) warns
  against greedily sampling the mix). Kept only as the observable degraded fallback.
- **A unified cross-domain candidate-quality pool for v1** — one score fusing objective need, template
  quality, readiness fit, tissue cost, and preference has units and tradeoffs that are hard to explain or
  calibrate, and it re-entangles allocation currency with state-update dose. Deferred until production
  evidence shows strict modality-first repeatedly picks poor concrete sessions when a near-equivalent
  modality had a materially better option (measure that offline first).
- **Reusing the concrete engine with no pure proposal seam** — the assignment layer would pick a modality
  the concrete engine then can't feasibly serve (no template fits the duration, equipment rules it out,
  spacing invalidates every candidate). The `preview_best_session` seam surfaces feasibility *before*
  assignment.
- **An untyped open constraint dict / a string explanation / one global numeric priority ladder** — all
  three become impossible to audit or extend; replaced by the typed constraint, the structured trace, and
  scope/time-aware precedence.

Composes with — but does not promote — the shadow MPC ([ADR-0042](0042-shadow-mpc-planner.md)), whose
receding-horizon re-ranking is the nearest existing pattern; realizes
[ADR-0050](0050-objectives-drive-training-emphasis.md); bounds
[ADR-0051](0051-user-owns-structure-engine-owns-safety.md).

**Guardrail:** the mix chooses *what kind* of session via feasibility-aware receding-horizon assignment
over soft slots; the existing engine chooses *which* session; only the next slot is issued and objective
intent is never counted twice. P11 consumes a typed `ResolvedPlanningConstraint` set and emits a
structured `PlanningDecisionTrace` — it never reads `PlanningOverride` rows, invents safety, or claims
objective-date costs. A safety-less slot yields no prescription, never an unsafe fill.
