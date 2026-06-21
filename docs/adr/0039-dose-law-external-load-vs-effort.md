---
status: proposed
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
