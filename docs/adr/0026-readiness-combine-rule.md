---
status: proposed
date: 2026-06-20
---
# How acute wellness combines with modeled fatigue

The single readiness number ([PDR-0005](../pdr/0005-one-backend-owned-readiness-number.md))
is `f(modeled fatigue/tissue + acute daily wellness)`. The open question is *how* the
acute daily-wellness signal (HRV, sleep, RHR, soreness, mood) combines with modeled
`F`/`T`: an **additive modifier** that nudges the modeled value, versus a
**cap/override** where a bad wellness day clamps readiness regardless of the model.

Deferred to its phase (P5) when daily-wellness ingestion lands and we can calibrate
against real data. Leaning additive-modifier (preserves model signal; less brittle than
a hard override), but not decided.

**Guardrail:** until accepted, keep the combine rule in one place
(`readiness_service`) so swapping it is a one-file change.
