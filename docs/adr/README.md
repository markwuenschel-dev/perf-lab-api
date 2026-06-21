# Architecture Decision Records (ADR)

ADRs record **how** Performance Lab is built — the architectural bets, integration
patterns, and deliberate deviations that a future reader would otherwise look at
and ask "why on earth did they do it this way?"

Product decisions ("**what** we build and for whom") live in [`../pdr/`](../pdr/).

## When to write one

All three must be true (otherwise it's a task, not a record):

1. **Hard to reverse** — changing your mind later has real cost.
2. **Surprising without context** — a future reader will wonder why.
3. **The result of a real trade-off** — there were genuine alternatives.

Cleanups, version bumps, and obvious fixes are **tasks**, not ADRs — keep this log
signal-dense.

## Format

One file per decision: `NNNN-slug.md`, numbered sequentially (scan for the highest
number and increment). Keep it lightweight — a few sentences is plenty.

```md
---
status: proposed | accepted | deprecated | superseded by ADR-NNNN
date: YYYY-MM-DD
---
# Short title of the decision

Context, what we decided, and why — 2-4 sentences. Name the rejected alternative.

**Guardrail:** the one rule this decision imposes on future work (only when it
constrains future changes).
```

`Considered Options` / `Consequences` sections are optional — add them only when
they earn their keep.

## Process

A `proposed` record is written in the **same PR** that embodies the decision
(by whoever — agent or human — makes it). The critic gates whether a change needs
one. The decision becomes `accepted` when the human **merges** the PR. Records live
and die with their PR.

## Index

| # | Decision | Status |
|---|----------|--------|
| [0001](0001-persist-state-history.md) | Persist state history, not just the latest state | accepted |
| [0002](0002-separate-logs-from-state.md) | Keep workout logs separate from athlete state | accepted |
| [0003](0003-explicit-dose-layer.md) | Use an explicit stress-dose layer `D(t)` | accepted |
| [0004](0004-separate-preview-and-mutation.md) | Keep preview and mutation separate at the API | accepted |
| [0005](0005-multidimensional-fatigue.md) | Model fatigue as multi-dimensional | accepted |
| [0006](0006-separate-capacity-fatigue-tissue.md) | Separate capacity, fatigue, and tissue stress | accepted |
| [0007](0007-legacy-scalar-mirrors.md) | Keep legacy scalar mirrors aligned with engine vectors | accepted |
| [0008](0008-weak-points-as-signals.md) | Store weak points as probabilistic signals | accepted |
| [0009](0009-weak-points-bias-not-hijack.md) | Weak points bias the prescriber, they don't hijack it | accepted |
| [0010](0010-separate-planning-and-adaptation.md) | Keep planning and adaptation separate | accepted |
| [0011](0011-lazy-planned-session-content.md) | Populate planned-session content lazily | accepted |
| [0012](0012-link-workouts-to-planned-sessions.md) | Link completed workouts to planned sessions | accepted |
| [0013](0013-benchmarks-as-measurement-layer.md) | Use benchmarks as a separate measurement layer | accepted |
| [0014](0014-kpis-as-snapshots.md) | Store derived KPIs as snapshots | accepted |
| [0015](0015-mappings-before-ekf.md) | Use observation mappings before full EKF complexity | accepted |
| [0016](0016-exercise-metadata-layer.md) | Use the exercise library as a metadata layer | accepted |
| [0017](0017-auth-outside-v1.md) | Keep auth outside `/v1` | accepted |
| [0018](0018-alembic-only-schema.md) | Use Alembic as the only schema manager | accepted |
| [0019](0019-thin-deprecated-modules.md) | Keep deprecated transition modules thin | accepted |
| [0020](0020-frontend-types-manual-mirror.md) | Frontend types are manual mirrors | superseded by ADR-0025 |
| [0021](0021-frontend-mirrors-backend-domain.md) | Keep the frontend control loop close to the backend domain | accepted |
| [0022](0022-legibility-over-cleverness.md) | Favor legibility over premature cleverness | accepted |
| [0023](0023-eight-capacity-axes-everywhere.md) | Eight capacity axes, engine to UI, no rollup | accepted |
| [0024](0024-canonical-units-imperial-pace.md) | Canonical units: sec/mile pace, 0–100 fatigue/tissue | accepted |
| [0025](0025-generate-ts-types-from-openapi.md) | Generate web TypeScript types from the OpenAPI schema | accepted |
| [0026](0026-readiness-combine-rule.md) | How acute wellness combines with modeled fatigue | proposed |
| [0027](0027-background-job-scheduler.md) | Background-job scheduler for the nightly wearable pull | proposed |
| [0028](0028-hosting-platform.md) | Hosting platform (Railway vs Render) | proposed |
| [0029](0029-periodization-intent-envelope.md) | Periodization lives as an intent envelope on the block | accepted |
| [0030](0030-block-derived-intent-modality-mix.md) | Training intent is block-derived; modality_mix drives concurrency | accepted |
| [0031](0031-prescription-seeds-the-log.md) | Prescription seeds the workout log | accepted |
| [0032](0032-relative-state-math-benchmark-anchored.md) | State math is relative now, calibrated later; benchmarks anchor | proposed |
| [0033](0033-training-builds-and-loses-capacity.md) | Workout-driven adaptation must move capacity; capacities detrain | accepted |
| [0034](0034-residual-based-benchmark-anchor.md) | Benchmark correction is residual-based; backend owns normalization | accepted |
| [0035](0035-benchmark-seeded-initial-state.md) | Initial state is benchmark-seeded; experience level is a weak prior | proposed |
| [0036](0036-per-axis-confidence-scalar.md) | Per-axis capacity confidence: a scalar now, EKF covariance later | accepted |
| [0037](0037-model-concurrent-interference.md) | Model concurrent-training interference (negative cross-talk) | accepted |
| [0038](0038-canonical-domain-taxonomy.md) | Canonical domain is the one taxonomy; the prescriber keys on it | accepted |
| [0039](0039-dose-law-external-load-vs-effort.md) | Dose-law intensity splits external load from internal effort | accepted |
