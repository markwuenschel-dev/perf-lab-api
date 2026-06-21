---
status: accepted
date: 2026-06-21
---
# Model concurrent-training interference (negative cross-talk)

The state engine applied only positive cross-talk (aerobic→work_capacity,
hypertrophy→max_strength, metabolic→work_capacity — mutual support). The interference
coefficients `INTERFERENCE_MET_ON_FORCE` and `INTERFERENCE_DAM_ON_POWER` were defined in
`cross_talk.py` but referenced **nowhere** ("for future use"). So the model treated
run + lift as purely additive: piling on endurance volume showed zero blunting of
strength/power progress. That leaves the concurrent thesis
([PDR-0001](../pdr/0001-concurrent-multidomain-thesis.md) /
[PDR-0002](../pdr/0002-domain-as-lens-over-one-body.md)) — a *cross-talk-aware* engine —
unbacked, and reduces [ADR-0030](0030-block-derived-intent-modality-mix.md)'s
`modality_mix` to a scheduler with no physiological consequence.

We wire interference in now as the keystone of concurrency: recent high metabolic/aerobic
dose reduces max_strength/power **adaptation efficiency** (the interference effect); high
structural damage suppresses power expression — implemented as suppression terms in
`_apply_adaptation_gains` (`state_update_v0`). This stays within the relative frame
([ADR-0032](0032-relative-state-math-benchmark-anchored.md)): interference is a
qualitative *sanity behavior* the model must exhibit
([ADR-0033](0033-training-builds-and-loses-capacity.md)'s "moves the right way" bar), not
a calibrated percentage, and is tuned by simulation. MVP is directional; the known
asymmetry (endurance blunts strength more than the reverse) and scheduling-sensitivity
(same-session worse than separated) are later refinements. We rejected deferring it
(leaves the product's core differentiator inert).

**Guardrail:** the engine must represent concurrent interference, not only synergy — a
conditioning-heavy `modality_mix` must visibly cost strength/power adaptation. Keep
interference relative/qualitative until benchmark calibration (B→A) justifies specific
magnitudes.
