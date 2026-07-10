---
status: proposed
date: 2026-07-10
---
# Per-axis seed uncertainty by evidence tier; debt and provisionality are views over live confidence

[ADR-0036](0036-per-axis-confidence-scalar.md) gave every capacity axis a live variance
(`CapacityConfidence`), but the seed writes a **uniform** `SEED_CAPACITY_VARIANCE` to all
eight — a squat-1RM-backed `max_strength` carries the same uncertainty as `skill`/`mobility`,
which are flat `50.0` with no evidence. And the P7 profile columns (`initial_seed_status`,
scalar `initial_seed_confidence`) risk becoming a **parallel** confidence authority. This ADR
resolves wayfinder #106: seed uncertainty *values*, and how debt/provisionality are expressed
without a second confidence system.

**One runtime authority.** Live per-axis `CapacityConfidence` (the variance itself — there is
no separate confidence field to disagree with it) is the **sole** source for residual gain,
measurement debt, provisional labels, and recommendation authority. The seed snapshot is
**immutable per-axis provenance** (`initial_seed_source_by_axis`,
`initial_seed_confidence_by_axis`, `evidence_tier_by_axis`, `policy_version`, `seeded_at`) —
never read at runtime for current provisionality (a static/dependency check forbids runtime
engine modules importing the snapshot accessor). Any scalar rollup (`initial_seed_status ∈
{none, experience_prior_only, benchmark_seeded, mixed}`, numeric summary) is an
explicitly-versioned analytics derivation. Migration never fabricates seed confidence from
*current* confidence (legacy → `legacy_unknown`); corrections are superseding events, not
silent overwrites (deferred machinery — a rare correction is a migration pre-launch).

**Seed variance = axis-scaled × evidence tier.** The uniform constant is retired for
`seed_variance(axis, tier) = axis_base_variance[axis] × tier_multiplier[tier]`, over **five
tiers** with a hard per-axis ordering enforced in code:
`R_validated_benchmark[a] < direct_measured_onramp < direct_estimated_onramp <
cross_axis_inference < experience_prior < unseeded`. `source_type`, `evidence_tier`, and
`variance` are **three separate fields** — no 1:1 collapse (the same source class varies in
quality by protocol/semantics). Cross-axis inference (power←squat) is **not** an experience
prior: it retains `source_observation_id`, `seed_group_id`, and `inference_model_version`.
Unseeded neutral values (`skill`/`mobility` = 50) are `source=none`, bounded-max variance,
**hidden computational placeholders — never observations, never displayed as known** (a true
uniform `[0,100]` prior has variance `100²/12 ≈ 833`; use a bounded/capped approximation, not
infinity).

**Diagonal covariance is a documented v1 approximation.** The live representation is diagonal,
so squat→{max_strength, power} correlated seed error cannot be stored. P10 **conservatively
inflates** the inferred variance and retains `seed_group_id` lineage; **no service may count
same-group axes as independent evidence** (measurement debt + analytics included). Full
`P₀ = J Σ Jᵀ + Q` covariance is deferred to the EKF ([ADR-0015](0015-mappings-before-ekf.md)).

**Debt and provisionality are separated views:**
- **`measurement_debt`** (evidence insufficient for a relevant decision, decision-relative
  `threshold(decision, axis)`) ⟂ **`actionable_measurement_debt`** (≥1 safe, valid, feasible
  assessment can reduce it). Debt can exist *without* being actionable — uncertainty is not
  hidden by benchmark unavailability. Only actionable debt is surfaced, ranked at the
  **benchmark** level: `eligible(b) = safe ∧ protocol_valid ∧ equipment ∧ capable`, then a
  normalized, versioned `utility(b) = wᵤ·Δuncertainty + w_d·decision_relevance + w_c·coverage −
  w_b·burden` (`information_gain_proxy_v1`). Top 1–3 surfaced progressively (cooldown, dismissal
  memory, diversity); hysteresis `τ_exit < τ_enter`; debt may re-enter as variance grows/ages.
- **`evidence_status`** (`measured|estimated|inferred|experience_prior|unobserved` — provenance)
  ⟂ **`confidence_status`** (`established|provisional|insufficient` — derived from **live
  variance only**). A value may be "measured but provisional"; a `measured` stamp can never
  override high live variance. No global `twin_is_provisional`. Recommendation-level
  provisionality is a separate versioned aggregation over the axes *material to that
  recommendation*, not a mean.

**Calibration is synthetic/expert, honestly labeled.** `seed_variance_policy_v1` ships with
`calibration_basis = synthetic_and_expert_prior`; a minimal harness proves tier monotonicity,
per-axis ordering, numerical stability, skip-all + bad-self-report + cross-axis sensitivity, and
debt-ranking stability — **not** empirical calibration (deferred until seed→retest data exists,
which needs elapsed time + intervening training exposure to separate seed error from adaptation).
Constants live in **separate** versioned policies (`seed_variance_policy_v1`,
`measurement_debt_policy_v1`, `confidence_presentation_policy_v1`,
`recommendation_uncertainty_policy_v1`) under an `uncertainty_policy_bundle_v1` umbrella, so
changing prompt count doesn't imply seed variances were re-calibrated.

Extends [ADR-0036](0036-per-axis-confidence-scalar.md); composes with ADR-0058 (onramp writes are
`initialize_prior`; `value_semantics` explains provenance, never determines live provisionality)
and #104 (seed provenance feeds the onboarding state machine).

**Guardrail:** one live confidence authority (`CapacityConfidence`); seed snapshot is immutable
provenance, never runtime-read. Seed uncertainty is axis-scaled and strictly ordered below any
validated measurement. Provenance, current uncertainty, debt existence, and prompt actionability
are four separate facts — none may masquerade as another.
