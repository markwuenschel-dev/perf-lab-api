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
label**. Two named rules share one definition of current provenance
(`observation_authority.is_demonstrated_strength`, SQL form in `benchmark_observation_repository`): the
public `best_currently_validated_e1rm` reports demonstrated strength only, and the decline machine's
prior adds the legacy migration's tests (below). Demonstrated strength is:

* a **measured max test**: `value_semantics = measured`, evidence `direct_measurement` or
  `protocol_grade_estimate`, source type `athlete_entry`, and not protocol-invalid;
* the row is **positively `valid`** and not quarantined. An unknown or pending status never
  qualifies: ingestion requires `valid`, and a pending row has received no state application;
* never an estimate: a training-derived e1RM, a reported estimate, or any `estimated` /
  `lower_bound` row, however high.

**History is preserved for the decline machine only, through the migration record.** `legacy_unknown`
is what the resolver writes for a source it does not recognise, and a028 itself calls the rows it
relabelled "ambiguous legacy history"; a025's measured/direct labels were assigned in bulk from
`source` and cannot prove a max. So `legacy_unknown` is never *demonstrated strength* and is never
reported as the public best-validated figure. Rows that carry the whole migration record
(`provenance_operation = schema_backfill`, `migration_version = a028`, the a028 resolution reason,
observation model `benchmark_protocol`, measured/direct, and not protocol-invalid) are
**decline-protection evidence** (`is_decline_protection_evidence`): they may be the prior a decline is
judged against, so an older athlete's next low test is still a candidate and not a silent "first
measurement". The decline prior is therefore `decline_prior_watermark` (demonstrated strength plus
those rows); the public figure is `demonstrated_watermark` (current provenance only). No live write
can produce the migration fields.

**A candidate is only as good as the evidence it was opened on.** It is *supported* only while all of
these hold, each recomputed from the rows as they are now: it was opened under the current decline
policy; its trigger is a valid, unquarantined measured test whose **effective authority** (the stored
effect met with the effect derived from the row's current provenance, as ingestion computes it) is
still bidirectional; the trigger still has the value and the time the candidate recorded (the retest
interval runs from `created_at`); its recorded prior equals the prior derivable for that moment
(decline-protection evidence dated at or before the trigger, excluding it); and nothing demonstrated
since the trigger has re-demonstrated at or above that prior (a test the machine never saw, because it
arrived record-only, must not leave the candidate's ceiling standing; the observation being processed
is left out, since the machine handles its own re-demonstration). A prior that was a training estimate
under the old watermark, a backdated higher test, a quarantine, a trigger corrected since: each makes
it unsupported, and a higher maximum today never rehabilitates it. An unsupported `active` candidate
is retired (`dismissed`, with the reason) when the next observation arrives, and until then it is
ignored by the prescription ceiling, which stays read-only.

Training estimates keep their own bar for "is this a PR" (`estimated_pr_baseline`), per formula:
estimates from different formulas are not comparable and a formula change is not progress.
