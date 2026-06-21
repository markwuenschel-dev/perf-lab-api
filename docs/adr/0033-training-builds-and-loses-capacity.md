---
status: accepted
date: 2026-06-21
---
# Workout-driven adaptation must move capacity; capacities detrain

Tracing the engine by hand: a hard session adds ~10–17 points of fatigue per axis but
only ~0.01–0.05 points of capacity (`adapt_coef` 0.012–0.025 × an O(1) adaptation
signal), so over a 4-week block modeled capacity is essentially flat — the only thing
that moves it meaningfully is a benchmark observation. And capacity has **no decay term**
(it only ratchets up via `min(ceiling, cur + gain)`), so the model cannot represent
detraining. Both violate the behavioral-correctness bar from
[ADR-0032](0032-relative-state-math-benchmark-anchored.md) (the relative signal must move
in the right direction and stay sane).

We decided: (1) **rebalance** workout-driven adaptation so a productive session moves
capacity by order ~0.2–1.0 points — a 4-week block yields a visible few-point gain
between tests — while benchmarks remain the authoritative correction; (2) add a **slow,
axis-specific capacity decay** (detraining) with long half-lives (aerobic detrains faster
than max-strength). We rejected leaving capacity movement to benchmarks only (the twin
shows no progress without testing, and detraining is unrepresentable).

Touches the state-update engine (`app/logic/state_update_v0.py`).

**Guardrail:** training must visibly build — and, with disuse, lose — modeled capacity
between benchmarks; benchmarks correct but are not the sole source of capacity change.
Choose the rebalanced magnitudes by simulating a representative block, not by eyeballing
constants.
