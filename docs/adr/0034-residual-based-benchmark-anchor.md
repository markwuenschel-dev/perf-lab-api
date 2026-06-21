---
status: accepted
date: 2026-06-21
---
# Benchmark correction is residual-based; the backend owns normalization

The observation→state mappings computed an *absolute* nudge from the raw value, not a
residual against what the model expected. With every seeded mapping `direct` and
`intercept=0`, `delta = coef·amp·tanh(signal/scale)` is positive for every observation:
a PR and a terrible result both push the target capacity **up** (`new_v = cur + delta`
is additive), the `tanh` saturates across the realistic range (a 16:40 and a 25:00 5k
nudge almost identically), and the magnitudes (~0.04–0.15 on a 0–100 axis) are no larger
than per-workout drift — so nothing moves capacity meaningfully and benchmarks **cannot
pull state down**. The anchor that [ADR-0032](0032-relative-state-math-benchmark-anchored.md)'s
B→A strategy depends on was structurally one-directional.

We decided:
1. **Residual-based correction.** Each definition gets a forward map (a simple linear
   `state → expected value`, or a stored baseline) so the engine computes
   `residual = normalized(measured) − expected` and nudges proportional to the **signed**
   residual. Below-expectation pulls state down, above-expectation pulls it up, with real
   discrimination. This stays pre-EKF ([ADR-0015](0015-mappings-before-ekf.md) intact),
   but the forward map's parameters become the explicit calibration target for B→A and
   every `(state, measured)` pair is training data.
2. **Backend-owned normalization.** The backend derives `normalized_value` from
   `raw_value` + the definition (unit, `better_direction`, population anchors); it no
   longer trusts a client-supplied normalized value for the state math.

We rejected retuning the absolute-nudge magnitudes (cheaper, but stays one-directional —
can never anchor or calibrate). Coverage is also only 13/36 defs mapped; closing that is
follow-on work, not a separate decision.

Realizes [PDR-0003](../pdr/0003-benchmarks-are-the-measurement-layer.md); builds on
[ADR-0013](0013-benchmarks-as-measurement-layer.md).

**Guardrail:** benchmark corrections are signed residuals against a model-predicted
expectation, never absolute one-directional nudges; the backend, not the client, computes
the normalized value used by the state math.
