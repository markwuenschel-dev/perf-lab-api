---
status: accepted
date: 2026-06-23
---
# How acute wellness combines with modeled fatigue

The single readiness number ([PDR-0005](../pdr/0005-one-backend-owned-readiness-number.md))
is `f(modeled fatigue/tissue + acute daily wellness)`. The open question was *how* the
acute daily-wellness signal (HRV, sleep, RHR, soreness, mood) combines with modeled
`F`/`T`: an **additive modifier** that nudges the modeled value, versus a
**cap/override** where a bad wellness day clamps readiness regardless of the model.

## Decision (P5, 2026-06-23)

**Additive modifier**, implemented in `app/services/readiness_service.py`:

1. The modeled readiness is the anchor: `R_model = overall_readiness(state) = 1 − mean_fatigue/100` (0–1).
2. Each present acute-wellness signal contributes a **direction-signed, clamped deviation**
   from the athlete's personal rolling baseline (trailing `BASELINE_WINDOW_DAYS`, falling
   back to a population default anchor when there's no history):
   `contribution = clamp(direction · (value − baseline) / norm, −1, +1)`.
3. The contributions are averaged and scaled by `WELLNESS_WEIGHT` (default 0.15), so wellness
   moves readiness by at most ±0.15: `R = clamp(R_model + modifier, 0, 1)`.

Chosen over cap/override because it **preserves the model signal** and degrades gracefully —
a single noisy wellness reading nudges rather than hijacks readiness. The scalar is `None`
when there is no modeled state (wellness modulates the model; it is not a standalone score).

**Provisional:** the per-signal `(direction, default_baseline, norm)` table, `WELLNESS_WEIGHT`,
and `BASELINE_WINDOW_DAYS` are first-pass values to be **calibrated against real data**. Per
the guardrail below they all live in one module, so calibration stays a localized edit.

**Guardrail (still holds):** the combine rule lives in exactly one place
(`readiness_service`); nothing else may recompute readiness.
