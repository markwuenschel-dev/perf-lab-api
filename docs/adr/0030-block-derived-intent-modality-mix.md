---
status: proposed
date: 2026-06-21
---
# Training intent is block-derived; modality_mix is the concurrency driver

`GET /v1/next-session` took its own `goal` query param, decoupled from the athlete's
active block — you could be mid-Running-block and request `Strength`, and nothing
reconciled them. Meanwhile `MesocycleBlock.modality_mix` (a weighted domain split) was
stored but **read by nothing**. We fix both together: when an active block exists, the
day's training intent derives from the block + today's `PlannedSession` (its
category/modality), and the `goal` param becomes a fallback for blockless users.
`modality_mix` is promoted from inert metadata to the driver of weekly-template
generation and per-day goal selection — which makes concurrent multi-domain training
(run + lift in the same week) first-class **now**, the near-term realization of
[PDR-0001](../pdr/0001-concurrent-multidomain-thesis.md) /
[PDR-0002](../pdr/0002-domain-as-lens-over-one-body.md). We rejected single-goal blocks
that defer concurrency to the still-unbuilt Objectives model (keeps the thesis/code gap
open). When [PDR-0004](../pdr/0004-objectives-first-class.md) Objectives land, they
*compute* `modality_mix` rather than the user setting it.

**Guardrail:** when a block is active, next-session intent comes from the block, not the
query param. `modality_mix` is authoritative for domain emphasis until Objectives
supersede it.
