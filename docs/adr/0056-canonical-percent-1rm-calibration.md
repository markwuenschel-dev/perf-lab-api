---
status: accepted
date: 2026-07-09
---
# One canonical, versioned %1RM ↔ load calibration service

P9 left **three** slightly different Epley forms in the tree, so the same set is interpreted
differently depending on which subsystem reads it:

| function | form | a true single |
|---|---|---|
| `e1rm_logic.percent_1rm` | `1/(1+(rtf−1)/30)` | 100% |
| `e1rm_logic.epley_e1rm` | `load·(1+(reps−1)/30)` | the load itself |
| `dose_engine._external_intensity_from_reps` | `1/(1+rtf/30)` | 96.8% |

A 1-rep max reads as both 100% and 96.8%; prescribed load and dose-inferred intensity for the
same set disagree by a few %. That is a production landmine.

## Decision

**One versioned calibration service** — `strength_calibration` — that everything calls:
prescription (`%e1RM → kg`), e1RM extraction (`load / %1RM`), and the
[ADR-0039](0039-dose-law-external-load-vs-effort.md) dose intensity fallback. It never returns
a bare number; every result carries `source` + `confidence` + `model_version`.

**Model ladder (highest-fidelity first):**

1. **Actual relative load** — `load / e1RM_pre` (dose only; primary when a pre-log e1RM
   exists, [ADR-0055](0055-strength-evidence-ledger.md)).
2. **RPE/RIR chart** — a static, versioned `reps × (RPE|RIR) → %1RM` table (Helms/RTS-shaped,
   e.g. `5 @ RPE8 ≈ 0.81`, `5 @ RPE10 ≈ 0.86`). **Primary for prescription**, and the dose
   fallback when effort is known. This is what lifters expect; Epley can't tell `5 @ RPE8`
   from `5 @ RPE10`.
3. **Reps-beyond-first Epley** — `%1RM = 1/(1+(rtf−1)/30)`, `rtf = reps + RIR`. Used **only**
   when the set is marked/assumed to-failure or AMRAP (a single to failure = 100%,
   self-consistent with `e1rm = load·(1+(reps−1)/30)`). **Not** the default for ordinary sets
   with no logged effort — that would be fake precision.
4. **Movement/program default** (if configured).
5. **Neutral / missing** — labeled `neutral_missing`, low confidence.

**Bounds:** clamp `%1RM ∈ [0.30, 1.05]`; for user-facing *prescription* clamp to `[0.30,
1.00]` (never prescribe >100% e1RM absent an explicit overload protocol). Effort fidelity
([ADR-0045](0045-per-set-catalog-bound-workout-logging.md)) lowers confidence: `group_level`
effort feeds the chart at reduced confidence and a stricter extraction gate.

**Retire** `dose_engine._external_intensity_from_reps` (the classic no-minus-one variant) and
fold `e1rm_logic.percent_1rm` / `epley_e1rm` into `strength_calibration` as internal fallback
models. Ships **with the ADR-0039 PR** (that PR already changes the intensity function); not
in the hotfix.

**No caller may implement its own reps/load/RPE/RIR → %1RM logic.** Prescription, extraction,
dose fallback intensity, and tests all call `strength_calibration`. Any new calibration model
requires a `model_version` bump **and** golden-case regression tests. The three ad-hoc Epley
sites are deleted, not wrapped-and-left.

## Consequences

- One place to recalibrate; `model_version` (`rpe_rir_chart_v1`) is emitted everywhere so
  future movement-specific / velocity-based curves are auditable.
- Invariant (narrowed, so it doesn't forbid legitimate downstream transforms): **the same set
  must resolve to the same base external intensity `I_set` everywhere.** Downstream consumers
  (Model A/B, fatigue vs tissue routing, prescription) may *transform* `I_set` with different
  exponents/weights, but may **not** recompute it with divergent formulas or hidden fallbacks.
- Golden cases (required regression tests): a single @ RPE10 / 0 RIR → **1.00**; known-effort
  estimates use the RPE/RIR chart; unknown-effort estimates use Epley **only** when the
  failure/AMRAP assumption is explicit; `5 @ RPE8 < 5 @ RPE10`; `5 @ 2RIR ≈ 5 @ RPE8`;
  prescription path and dose fallback agree for identical input.

## Delivered (2026-07-10, with the ADR-0039 PR)

`app/logic/strength_calibration.py` is the sole home for reps/load/RPE/RIR → %1RM. It owns
`external_intensity_for_set` (the ladder), `percent_1rm_for_prescription` (chart, clamped
`[0.30, 1.00]`), `suggested_load_kg`, `epley_e1rm`, and `is_loaded`, with a versioned
`RPE_CHART` (`model_version = "rpe_rir_chart_v1"`). Every result is a `CalibrationResult`
(value + source + confidence + model_version). The three ad-hoc sites are **deleted**:
`app/logic/e1rm.py` is removed, `dose_engine._external_intensity_from_reps` is gone, and
prescription/extraction/dose all call the service. Golden cases live in `tests/test_dose_split.py`.

## Amendment (2026-10-09, P4-2b): chart estimates for new set-derived evidence

Until now the e1RM stored for a set was Epley (`e1rm_from_set`) whatever effort the athlete
gave; effort only gated admission and scaled confidence. With `E1RM_CHART_ESTIMATES=true`
(default **off**) a set with a known, self-consistent effort (`resolve_effort`) that also clears
the extraction gate (`is_e1rm_informative`: at most 5 reps, effort 8 or more with per-set
provenance, 9 or more otherwise) is stored as `load / chart(reps, effort)`, rounded to
0.1 kg, `formula = rpe_rir_chart`, `model_version = rpe_rir_chart_v1`. Anything else (flag off,
unknown or contradicting effort, a set the gate rejects) is the legacy Epley estimate, exactly
as before. A set the gate rejects cannot size a load, and the chart is 16-27% above Epley for
the easy or high-rep sets, so re-estimating those would only move the profile projection and the
onboarding seed by the largest steps.

- **One entry point.** `estimate_set_e1rm` (`app/services/e1rm_estimation.py`) is called by
  workout extraction, a set reported in Assess and the onboarding seed. The route cannot change
  the number a set produces.
- **Not the dose ladder inverted.** The estimate takes no pre-log e1RM. Inverting
  `external_intensity_for_set` when it selects `load / e1rm_pre` would return the estimate it
  started from.
- **History is not rewritten.** Existing rows keep their value, formula and labels. There is no
  recompute and no superseding row.
- **Semantics.** A chart row is `modeled_estimate` / `estimated`, personal record or not. It is
  a model's point estimate, not a lower bound, and may overshoot. Legacy Epley rows keep their
  labels (a PR is still `lower_bound`).
- **Retry identity** is the submitted facts and method (lift, mode, time, load, reps, RPE, RIR),
  not the derived value, so a submission recorded under one formula is recognised under the other.

**Coexistence (mixed formulas in the same window):**

| reader | policy |
|---|---|
| prescription basis | formula-blind: the highest eligible value in the 28-day window wins. The first new qualifying chart estimate moves an athlete's basis by the whole step, at once |
| dose denominator (`e1rm_pre`) | the same selection, so a higher basis lowers `load / e1rm_pre` by the same factor. Freezing the v0 dose operator does not freeze this denominator |
| estimated-PR tracking | compared only with estimates of the same formula |
| decline watermark, public best-validated | demonstrated strength only (ADR-0066); a chart estimate is never one |
| capacity authority | none (ADR-0055 amendment) |
| profile projection | latest report, as before |

Rollback: turning the flag off stops new chart rows. Chart rows already written stay eligible
for prescription until they age out of the window. The step is not training progress and must
not be presented as such. `python -m app.scripts.e1rm_activation_report` shows each athlete's
step (today's basis against the chart's, and the factor `load / e1rm_pre` is multiplied by)
before the flag is turned on; it is read-only.

Size of the step for a set that clears the gate (1–5 reps, this repository's chart): about
0–2% at RPE 10, 5% at RPE 9, 8.5–9% at RPE 8. Always upward. The KPI, anchor and objective readers were not
changed by this slice; whether they should exclude modeled estimates is open.
