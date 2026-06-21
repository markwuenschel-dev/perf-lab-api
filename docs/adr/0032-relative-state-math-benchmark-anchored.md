---
status: proposed
date: 2026-06-21
---
# State math is relative now, physiologically calibrated later; benchmarks are the anchor

The engine's state math is built from hand-set, uncalibrated constants (dose exponents
`α/β/γ/ρ`, the `_shape_six` modality coefficients, fatigue/tissue decay half-lives,
capacity ceilings) and the state scale has no physical anchor — "aerobic = 65" means
nothing in real units. We accept this **deliberately**: the modeled state is a
*relative, self-consistent* latent signal whose correctness test is behavioral — it must
move in the right direction, rank sessions sensibly, and stay stable — not
physiologically accurate. We do not yet have the labeled outcome data to fit a predictive
model, and presenting the magic numbers as physiology would be false precision. The
benchmark system ([PDR-0003](../pdr/0003-benchmarks-are-the-measurement-layer.md)) is the
one tie to ground truth: observation→state mappings correct the latent state toward
measured reality, and accumulated benchmark observations become the dataset that later
calibrates the constants.

The explicit trajectory is **B → A**: relative today, physiologically
calibrated/predictive (state maps to VO₂max / estimated 1RM / pace; constants fit to
data) as the goal. We rejected calibrating now (no data — premature) and staying
permanently relative (caps the product's eventual value).

Relates to [ADR-0013](0013-benchmarks-as-measurement-layer.md),
[ADR-0015](0015-mappings-before-ekf.md),
[ADR-0024](0024-canonical-units-imperial-pace.md).

**Guardrail:** treat engine constants as relative placeholders, not physiological truth.
Do not surface modeled state to the user as a real-world number (VO₂max, kg) until it is
calibrated against benchmarks. Every benchmark observation must remain usable as future
calibration data.
