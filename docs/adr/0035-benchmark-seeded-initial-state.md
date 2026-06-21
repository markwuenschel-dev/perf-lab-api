---
status: proposed
date: 2026-06-21
---
# Initial state is benchmark-seeded; experience level is a weak prior

The baseline seed (`state_service._build_baseline_vector`) derives all eight capacity
axes from four legacy scalars chosen by a 4-level `experience_level` enum, refined only by
squat 1RM (→ force) and 5k time (→ aerobic); the other six axes (glycolytic, hypertrophy,
power, mobility, work_capacity, skill) are a deterministic function of the experience
bucket. Because modeled capacity evolves slowly
([ADR-0033](0033-training-builds-and-loses-capacity.md)), that bucket-driven sameness
persists — two athletes of different sports but equal "experience" get near-identical
twins.

We decided the initial state is seeded from the onboarding **benchmark onramp**
([PDR-0009](../pdr/0009-onboarding-benchmark-onramp.md)): each entered/tested benchmark
sets its target axes via the residual anchor
([ADR-0034](0034-residual-based-benchmark-anchor.md)). The experience / 1RM / 5k seed
remains a **low-confidence prior** that benchmark and workout data overwrite quickly; it
is explicitly not ground truth. We rejected expanding the onboarding questionnaire (more
friction, still abstract guesses, no per-axis measurement).

This requires a notion of seed/axis confidence so a weak prior *yields* to data rather
than competing with it as an equal — the lightweight mechanism is decided next; full EKF
covariance stays deferred ([ADR-0015](0015-mappings-before-ekf.md)).

**Guardrail:** the experience-level seed is a low-confidence prior, not ground truth.
Benchmark-sourced state must dominate it as soon as any benchmark exists for an axis.
