# Product Decision Records (PDR)

PDRs record **what** Performance Lab is and **who** it's for — the product thesis,
scope boundaries, and the first-class concepts the product commits to.

Architecture decisions ("**how** it's built") live in [`../adr/`](../adr/).

PDRs use the **same format and process** as ADRs — see
[`../adr/README.md`](../adr/README.md). A record is a PDR when the decision is about
product shape, users, or scope rather than implementation. When a product decision
implies an architectural one, write both and cross-link them
(e.g. [PDR-0003](0003-benchmarks-are-the-measurement-layer.md) ↔
[ADR-0013](../adr/0013-benchmarks-as-measurement-layer.md)).

## Index

| # | Decision | Status |
|---|----------|--------|
| [0001](0001-concurrent-multidomain-thesis.md) | Perf Lab is a concurrent, multi-domain adaptive engine | accepted |
| [0002](0002-domain-as-lens-over-one-body.md) | Domain is a lens over one body, not a mode switch | accepted |
| [0003](0003-benchmarks-are-the-measurement-layer.md) | The benchmark system is the measurement layer | accepted |
| [0004](0004-objectives-first-class.md) | Objectives are a first-class model | accepted |
| [0005](0005-one-backend-owned-readiness-number.md) | One backend-owned readiness number | accepted |
| [0006](0006-wearable-sync-cloud-api-first.md) | Wearable sync is cloud-API providers first | accepted |
| [0007](0007-first-wearable-provider.md) | First wearable provider to integrate | proposed |
| [0008](0008-plan-is-a-seed-not-a-rail.md) | The training plan is a seed, not a rail | proposed |
| [0009](0009-onboarding-benchmark-onramp.md) | Onboarding establishes baselines through a benchmark onramp | proposed |
