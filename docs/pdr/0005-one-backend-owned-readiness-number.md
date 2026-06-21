---
status: accepted
date: 2026-06-19
---
# One backend-owned readiness number

There is **one** readiness scalar, owned and computed by the backend, surfaced
everywhere the product shows "readiness." It is `f(modeled fatigue/tissue + acute daily
wellness)`, where daily wellness (HRV / sleep / RHR / soreness / mood) is a first-class
engine input. The web app must not compute its own divergent readiness formulas.

The *combine rule* (how acute wellness mixes with modeled fatigue) is an open
architectural decision — [ADR-0026](../adr/0026-readiness-combine-rule.md).

**Guardrail:** readiness is read from the backend, never recomputed client-side.
