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
estimates included. An estimate is a model's extrapolation, not a lift: Epley could already
overshoot what an athlete can do, and the chart estimate of ADR-0056 will too. Judged against such
a prior, an honest test looks like a decline. **Demonstrated strength is now provenance, not a
label**, and one definition serves both the decline machine's prior and
`best_currently_validated_e1rm` (`observation_authority.is_demonstrated_strength`, SQL form in
`benchmark_observation_repository`):

* a **measured max test**: `value_semantics = measured`, evidence `direct_measurement` or
  `protocol_grade_estimate`, source type `athlete_entry`, and not protocol-invalid;
* the row is **positively `valid`** and not quarantined. An unknown or pending status never
  qualifies: ingestion requires `valid`, and a pending row has received no state application;
* never an estimate: a training-derived e1RM, a reported estimate, or any `estimated` /
  `lower_bound` row, however high.

**History is preserved through the migration record, not a source label.** `legacy_unknown` is
what the resolver writes for a source it does not recognise, and a028 itself calls the rows it
relabelled "ambiguous legacy history"; a025's measured/direct labels were assigned in bulk from
`source` and cannot prove a max. So `legacy_unknown` does not qualify by itself. Rows that carry
the whole migration record do (`provenance_operation = schema_backfill`, `migration_version =
a028`, the a028 resolution reason, observation model `benchmark_protocol`, measured/direct): a
named compatibility rule (`is_migrated_legacy_test`) that keeps an older athlete's pre-migration
tests as their decline protection, since without a watermark the next low test would pass through
as a "first measurement". No live write can produce those fields.

**A candidate is only as good as its prior.** An `active` candidate whose recorded prior is no
longer demonstrated (it was opened against a training estimate under the old watermark, or the
row behind it has since been quarantined) is retired (`dismissed`, reason
`prior_not_demonstrated`) before it can confirm a regression, and it is ignored by the
prescription ceiling, which stays read-only and leaves the retirement to the next observation.

Training estimates keep their own bar for "is this a PR" (`estimated_pr_baseline`), per formula:
estimates from different formulas are not comparable and a formula change is not progress.
