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
