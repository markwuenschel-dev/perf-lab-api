---
status: accepted
date: 2026-07-05
---
# Shadow EKF: full joint covariance over X/F/T

[ADR-0036](0036-per-axis-confidence-scalar.md) tracks a **scalar per-axis variance** for
capacity only, explicitly as "the honest pre-EKF stepping stone… generalizes to the EKF's
covariance with no throwaway," and [ADR-0015](0015-mappings-before-ekf.md) deferred the
full filter. This ADR builds the generalization — but as a **parallel, shadow-only
estimator**, changing nothing an athlete sees.

We add an Extended Kalman Filter over the full state `S = (X, F, T)` (22 dims): a joint
covariance `P` with a **predict** step (covariance propagated through the deterministic
twin plus process noise) and an **update** step (joint benchmark correction). It runs
alongside the production engine on the same dose/observation stream and writes belief
snapshots to `ekf_shadow_log`; `decision_impact` is always `none_shadow_only`.

**Key decisions**

- **The transition model *is* `update_athlete_state`.** The Jacobian `A = ∂f/∂s` is a
  finite difference around the real function, so the EKF's dynamics can never drift from
  production. It adds a covariance channel over the identical mean trajectory (a fresh
  predict's denormalized mean equals the engine's output exactly).
- **Normalized per-axis space.** All EKF math runs in each-axis/scale coordinates
  (capacity/ceiling, fatigue&tissue/100), matching the relative [0,1] residual semantics
  of [ADR-0034](0034-residual-based-benchmark-anchor.md)/0036. This makes the benchmark
  observation model trivial — `H = e_key`, innovation `= score01 − mean_key`, measurement
  noise `R = effective_variance / mapping_strength²` (reused verbatim from
  `benchmark_validity`). The single-axis EKF then reduces **exactly** to the production
  scalar residual anchor; the full `P` additionally shrinks *correlated* axes (and, via the
  `∂X/∂F` coupling the twin already has through adaptation efficiency and interference,
  lets fatigue uncertainty inform capacity uncertainty) — which the per-axis scalar loop
  cannot.
- **Covariance hygiene.** Symmetrize + diagonal-clamp + PSD-project each step; Joseph-form
  updates. Capacity process noise/bounds reuse the ADR-0036 dicts; fatigue/tissue get their
  own (larger) EKF process noise and seed variance.
- **Prove-it-in-shadow.** Calibration is the gate: aggregate NIS (`νᵀS⁻¹ν`, χ²-consistent
  at `E[NIS]=dim(y)`) on production `ekf_shadow_log` rows, and interval coverage from a
  DB-free replay harness. Verdict `promote | stay_shadow` follows the model-card contract,
  but promotion is out of scope for this arc.

**Rejected / deferred:** parameter uncertainty `P^θ` and hierarchical per-athlete `θ_i`
(the other frontier — engine params stay global); UKF/particle/HMM; MPC; any production
behavior change. Interference stays the ADR-0037 per-axis exponential — the EKF just
linearizes whatever `f` does. The Banister reservoir `s_struct_signal` is carried in the
transition context but excluded from the covariance in v1.

**Guardrail:** the shadow EKF must never write to production state or a prescription, must
be best-effort (a failure cannot break workout ingest or benchmark assimilation), and the
production scalar path (`capacity_confidence`, `_grow_confidence_variance`,
`_apply_capacity_residual`) stays untouched until a deliberate, calibration-backed decision
to promote.
