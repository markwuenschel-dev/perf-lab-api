---
status: proposed
date: 2026-07-12
---
# A single low benchmark must not durably regress max_strength

Before this, a single protocol-valid `bidirectional_update` benchmark below the model's
expectation regressed `capacity_x.max_strength` immediately and durably (and, via the
latest-raw e1RM ledger, dropped the prescription basis) — the P9 state-corruption class,
re-emergent (INT-02). 1RM testing carries real within-athlete variability (median CV ≈ 4.2%,
up to ~12.1%; Grgic et al., 2020), so one low result is often fatigue, protocol, or noise —
not durable capacity loss. Authority (`bidirectional_update`, "this evidence is *eligible* to
support a decrease", ADR-0058) is now separated from **application**: a first material low
observation opens a `strength_decline_candidate`, holds the axis (no regression), and records
`applied_capacity_effect = none`; a durable, **bounded** decline is applied only after an
**independent** corroborating observation (distinct occasion, ≥ the definition's minimum retest
interval with a versioned fallback), through a variance-aware estimator — never by overwriting
state with the low value. A re-demonstration dismisses the candidate; the window expiring
retires it; a severe unexplained drop routes to the existing safety surface rather than
auto-detraining. Materiality is `max(measurement_error, z_down·√(prior_var+obs_var))` with a
protocol MDC→SEM→provisional-fallback hierarchy, in raw e1RM units against the best *currently
valid* demonstrated watermark (`best_currently_validated_e1rm`); `max_strength` is the current
latent estimate, held separately from that watermark.

We **rejected** an EWMA watermark (obscures elapsed time, conflates protocols/variances, a run
of fatigued tests still drags it, opaque smoothing) and a monotone floor on *current* capacity
(would block genuine post-detraining/injury decline → unsafe prescriptions). The prescription
half is staged behind `DECLINE_CANDIDATE_PRESCRIPTION_BASIS` (off → shadow → on); the
correctness half (no first-obs regression, confirmed bounded update) ships live because it
strictly tightens the existing corruption.

**Guardrail:** a single observation never durably regresses current strength; durable downward
capacity updates require independent corroborating protocol-valid evidence and a bounded
variance-aware estimator move. Thresholds are protocol/uncertainty-derived and, absent
calibration, explicitly provisional (`strength_decline_policy_v1`, `synthetic_and_expert_prior`)
— a global percentage retune is out of scope and requires shadow calibration first.

## Amendment (2026-10-09, P4-2a): what "demonstrated" means

The decline watermark was `max(raw_value)` over every valid row of the lift, workout-derived
estimates included. That held only while a training estimate could not exceed what the athlete
can lift. Once estimates can (the chart-based e1RM of ADR-0056, P4-2b), a high estimate would make
an honest test look like a decline. **Demonstrated strength is now provenance, not a label**, and
one definition serves both the decline machine's prior and `best_currently_validated_e1rm`
(`observation_authority.is_demonstrated_strength`, SQL form in `benchmark_observation_repository`):

* a **measured max test**: `value_semantics = measured`, evidence `direct_measurement` or
  `protocol_grade_estimate`, source type `athlete_entry`, and not protocol-invalid;
* **legacy history is preserved**: rows migrated to `legacy_unknown` with measured/direct evidence
  still count, so an athlete's pre-migration tests keep their decline protection (dropping them
  would turn the next low test into a silent "first measurement");
* never an estimate: a training-derived e1RM, a reported estimate, or any `estimated` /
  `lower_bound` row, however high.

Training estimates keep their own bar for "is this a PR" (`estimated_pr_baseline`), per formula:
estimates from different formulas are not comparable and a formula change is not progress.
