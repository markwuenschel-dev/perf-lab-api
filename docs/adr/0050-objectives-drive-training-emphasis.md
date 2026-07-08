---
status: proposed
date: 2026-07-07
---
# Objectives drive concurrent training emphasis

The `Objective` model is fully built (multi, concurrent, cross-domain: benchmark + target +
date + priority), but it barely reached the prescriber: the day's content came from a
*single* resolved goal (block → query → `primary_goal` → default), and all active objectives
together contributed only a taper and one `+0.15` domain boost. [ADR-0030](0030-block-derived-intent-modality-mix.md)
explicitly deferred the real integration — `modality_mix` stays authoritative "until
Objectives supersede it" — and that supersession was never built. So multi-goal was
cosmetic: the UI showed many objectives while the planner trained one. That is worse than
not supporting objectives at all.

Active objectives now **compute the training emphasis.** Each contributes a weighted modality
vector by `priority × proximity-to-date × gap-to-target × status`; the weighted, **smoothed**
(hysteresis, so the plan doesn't thrash week to week) blend becomes the block-level
`modality_mix` that drives **multi-domain candidate generation** — running *and* strength
*and* support candidates scored against the blended state, not one goal's workout. A 5k
objective (priority 1, near) and a squat-total objective (priority 2, far) yield a run-leaning
blend that shifts to strength automatically as the 5k passes and the squat date nears — no
manual mode switch. Before candidate generation the mix passes through **safety constraints,
phase logic, and minimum-effective-dose floors** (so a secondary objective is never fully
neglected). `primary_goal`/`block_goal` demote to a fallback used only when no structured
objectives exist. We rejected keeping objectives as nudges (product dishonesty).

This realizes [PDR-0004](../pdr/0004-objectives-first-class.md) and completes the
[ADR-0030](0030-block-derived-intent-modality-mix.md) supersession. What a user with several
objectives is really asking is not "what is my one goal?" but "how should my limited capacity
be allocated across competing objectives over time?" — which is exactly what `modality_mix`
represents.

**Guardrail:** when structured objectives exist, they compute the `modality_mix` the
prescriber pursues — never fall back to a single primary goal, and never let both compete as
sources of truth. Objectives are prescription-driving control inputs, not labels.
