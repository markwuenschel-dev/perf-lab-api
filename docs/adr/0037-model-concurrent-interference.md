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

---

## Amendment (2026-07-06): interference keys on *excess* concurrent load

**Status: accepted.**

### Decision

Refine the concurrent-interference model so that suppression of strength adaptation is
keyed to incremental off-axis endurance/metabolic load **beyond a block-compatible
baseline**, rather than raw total fatigue.

### Context

The prior proxy used the raw endurance-load fraction (`0.4·metabolic + 0.6·structural`) as
the interference input. Because a hard strength block itself generates structural/metabolic
fatigue, the block **self-penalized**: the structural fatigue necessary for strength
adaptation was counted as interference against strength adaptation. Simulation showed
parameter-only tuning could only barely satisfy the guardrail (ratio 0.799 vs required
< 0.80, with strength build 2.06 vs 2.16 today) — insufficient margin, and it slowed
strength development.

### New model

Interference load for the strength/hypertrophy axes:

    Z_interference = max(0, E − z0)

where `E` is the off-axis endurance-load fraction (`_endurance_load_fraction`) and `z0`
(`interference_baseline_z0`) is the block-compatible baseline a hard strength block itself
produces (in load-fraction units, ~0.21 observed → 0.15 set with headroom). Strength
adaptation suppression is then the same exponential authority applied to the excess:

    I_strength = floor + (1 − floor) · exp(−alpha · Z_interference)

Initial parameters: `z0 = 0.15`, `interference_e_on_strength_alpha = 4.0`,
`interference_floor_by_axis[max_strength] = interference_floor_by_axis[hypertrophy] = 0.20`.
Scoped to the strength/hypertrophy path; the `power` endurance channel is unchanged.

### Consequences

- Hard strength blocks no longer self-trigger concurrent-interference penalties.
- Real endurance interference remains penalized — the guardrail clears with margin
  (simulated ratio 0.732, strength build 2.86).
- Touches `app/logic/interference.py`, `app/engine/parameters.py`, and this doc; adds one
  interpretable parameter `interference_baseline_z0`.
