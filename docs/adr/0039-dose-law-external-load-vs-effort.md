---
status: accepted
delivery: not-delivered
date: 2026-06-21
---
# Dose-law intensity splits external load from internal effort

The dose law `base = w_phi · log1p(V) · I^α · Δ^β · N^γ · F^ρ` double-counted intensity:
`I = session_rpe/10` and `F = (10 − avg_rir)/10 or I`. RPE and RIR are the same dimension
(RPE 8 ≈ RIR 2), so `I ≈ F` by construction; with RIR absent `F = I` exactly, making
`I^1.2 · F^1.0 = I^2.2` — effort squared. Meanwhile `load_kg` was captured but used only
in the volume proxy, so the one genuinely independent intensity signal (external load) was
ignored.

We split the two terms into what they should measure:
- **`I` = external intensity** — load relative to capacity (`load_kg / estimated_1RM` for
  lifts; pace vs threshold for runs), the estimated max sourced from the capacity/benchmark
  system.
- **`F` = internal effort** — proximity to failure from RPE/RIR.

These are independent (5 reps at 70% to failure ≠ 5 reps at 90% with 2 in reserve) and both
matter. This makes the `load_kg` that [ADR-0031](0031-prescription-seeds-the-log.md) seeds
from the prescription actually drive intensity. When external intensity is unknown (ad-hoc,
no load), fall back to effort-only — use `F`, set `I` to a neutral 1 — so the engine stops
double-counting rather than squaring effort. We rejected collapsing both into one effort
term (simpler, but discards the external-load signal — can't tell a heavy low-RPE single
from a light high-RPE set).

Within the relative frame of [ADR-0032](0032-relative-state-math-benchmark-anchored.md) —
the exponents stay simulation-tuned, not calibrated.

**Guardrail:** external load and internal effort are distinct dose inputs; never derive
both from the same RPE/RIR signal. With no external-load data, degrade to effort-only — do
not raise effort to a compounded power.

---

## Delivery status (2026-07-09): NOT DELIVERED — reopened

The decision above was accepted 2026-06-21 but **never implemented**. `calculate_stress_dose`
hardcodes `external_intensity = 1.0` **unconditionally** (`app/logic/dose_engine_v0.py`),
not only in the no-load fallback the text intended — so the load-present branch (`I =
load / e1RM`) has never run. The per-exercise path *does* compute a real external intensity
(`_build_exercise_doses`) but its result is discarded; only its φ vectors are harvested.
**Consequence:** at fixed volume, a light `5×5 @ 55%` and a heavy `5×5 @ 90%` produce the
same session dose. P9 (PR #96) claimed to close this loop; it did not — it fed *volume*
(`total_volume_load`) and φ, but the intensity term `I` stayed `1.0`. That claim was false
and is retracted here.

**This ADR is reopened.** It closes with **Model A** (session-scalar external intensity):

1. Logged set data produces a set-level external intensity value.
2. A weighted session-level `external_intensity` **enters the session dose base**, replacing
   the hardcoded `1.0`.
3. Equal-volume sessions at different relative intensities produce **different** dose.
4. Tests cover low / moderate / high intensity (`5×5 @ 50/75/90%`).
5. The dose explanation emits, for every set/session, the intensity value **and its
   provenance**: the e1RM **denominator** (source + observation id + value-semantics), the
   **fallback path** taken, the **calibration `model_version`**, and **confidence**. A missing
   denominator is `I = 1.0` labeled `neutral_missing` — **never** an unlabeled `1.0` that reads
   as "moderate intensity" when it means "unknown".

**Closure is brutally narrow — to prevent a repeat false closure:** ADR-0039 closes *only* when
external intensity is wired into `calculate_stress_dose`, `external_intensity` is no longer
hardcoded `1.0`, equal-volume/different-intensity sessions differ, and the above provenance is
emitted. **ADR-0039 does not close per-exercise φ routing — that is [ADR-0054](0054-per-exercise-dose-routing.md).**

**Scope (narrow, on purpose):** ADR-0039 is *session-level external intensity integration
only*. It does **not** claim per-exercise intensity is routed through each exercise's own φ
vectors — under Model A a session's intensity still flows through the aggregate-φ / derived-
modality shaping path, so a hard accessory partially inherits the session's shape. That
routing limitation is tracked by **[ADR-0054](0054-per-exercise-dose-routing.md)** and must
be documented as a `known_limitation` in the dose output.

**Dependencies / order:** the intensity denominator (`I = load / e1RM_pre`) must read an
**uncorrupted** pre-log e1RM, so the strength-evidence hotfix
([ADR-0055](0055-strength-evidence-ledger.md)) lands **first**. The intensity function itself
is the one canonical calibration service of
[ADR-0056](0056-canonical-percent-1rm-calibration.md). `I` uses `load / e1RM_pre` when a
pre-log e1RM exists, else the ADR-0056 RPE/RIR chart, else reps-beyond-first Epley (only when
the set is to-failure/AMRAP), else a labeled neutral. Set→exercise→session aggregation weights
by `w = reps · load`; `_external_intensity_from_reps`'s classic `1/(1+rtf/30)` form is retired.
