---
status: accepted
date: 2026-06-19
---
# The benchmark system is the measurement layer

`/v1/benchmarks/*` + `/v1/dashboard/*` are **the** measurement layer for the product.
The running-only Field Test (`/compute-metrics`) becomes one benchmark + an onramp; it
is retired as a *separate* source of truth. All "how am I measured / how am I
progressing" product surfaces read from the benchmark + dashboard system.

The architectural counterpart is
[ADR-0013](../adr/0013-benchmarks-as-measurement-layer.md).

**Guardrail:** new measurement/progress surfaces consume the benchmark + dashboard
system — do not add parallel measurement sources of truth.
