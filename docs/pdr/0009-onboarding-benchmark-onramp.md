---
status: proposed
date: 2026-06-21
---
# Onboarding establishes baselines through a benchmark onramp

A new athlete's first job is to establish baselines, not answer a questionnaire.
Onboarding routes into a short, domain-relevant **benchmark onramp** — pick
objectives/domains, then enter or test a few key benchmarks per active domain — because
the modeled state barely moves from training alone
([ADR-0033](../adr/0033-training-builds-and-loses-capacity.md)), so the *seed* dominates
the twin for a long time, and benchmarks are how we measure an athlete
([PDR-0003](0003-benchmarks-are-the-measurement-layer.md)). A 4-level experience dropdown
cannot differentiate an intermediate powerlifter from an intermediate marathoner; their
benchmarks can. The quick experience / 1RM / 5k seed stays as a zero-friction fallback
for users who skip the onramp. This generalizes the running-only Field Test into a
multi-domain onramp.

Implemented by [ADR-0035](../adr/0035-benchmark-seeded-initial-state.md); builds on
[PDR-0008](0008-plan-is-a-seed-not-a-rail.md).

**Guardrail:** onboarding's primary path is establishing baselines via benchmarks. Never
let the seed depend solely on a coarse self-rated experience level when a benchmark could
measure the axis.
