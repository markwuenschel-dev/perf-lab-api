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
| [0028](0028-hosting-platform.md) | Hosting platform (Railway vs Render) | superseded (moved to EC2, see `docs/DEPLOY.md`) |
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
| [0040](0040-macrocycle-thin-container.md) | The macrocycle is a thin container, not a materialized plan | proposed |
| [0041](0041-shadow-ekf-state-covariance.md) | Shadow EKF: full joint covariance over X/F/T (scalar → covariance) | accepted |
| [0042](0042-shadow-mpc-planner.md) | Shadow MPC planner: receding-horizon re-ranking of prescriber candidates | accepted |
| [0043](0043-hierarchical-per-athlete-parameters.md) | Hierarchical per-athlete parameters (θ_i): partial-pooled recovery β, shadow-only | accepted |
| [0044](0044-wearable-token-storage.md) | Wearable token storage: Fernet at-rest + OAuth state | accepted |
| [0045](0045-per-set-catalog-bound-workout-logging.md) | Workout logging is per-set, catalog-bound, and modality-heterogeneous | proposed |
| [0046](0046-skill-state-domain-filtered-projection.md) | Skill state is a domain-filtered projection over measured evidence | proposed |
| [0047](0047-one-benchmark-assessment-surface.md) | One benchmark assessment surface; no domain-specific seeders | proposed |
| [0048](0048-confidence-gates-recommendations.md) | Confidence gates recommendations — continuous ceiling + discrete claim thresholds | proposed |
| [0049](0049-missing-wellness-is-a-gap-not-imputed.md) | Missing wellness signals are gaps, not imputed | proposed |
| [0050](0050-objectives-drive-training-emphasis.md) | Objectives drive concurrent training emphasis (compute modality_mix) | proposed |
| [0051](0051-user-owns-structure-engine-owns-safety.md) | The user owns intent and structure; the engine owns safety and execution | proposed |
| [0052](0052-readiness-confidence-report-only.md) | Readiness confidence is a report-only reliability object; gating waits for P13 | accepted |
| [0053](0053-wellness-signal-registry.md) | Canonical wellness signal registry, categories, and implicit tracking | accepted |
| [0054](0054-per-exercise-dose-routing.md) | Per-exercise dose routing through exercise φ vectors | accepted |
| [0055](0055-strength-evidence-ledger.md) | Training-derived e1RM is estimated lower-bound evidence, not benchmark measurement | accepted |
| [0056](0056-canonical-percent-1rm-calibration.md) | One canonical, versioned %1RM ↔ load calibration service | accepted |
| [0057](0057-domaincode-three-roles-one-vocabulary.md) | DomainCode is one vocabulary with three distinct roles | proposed |
| [0058](0058-observation-provenance-capacity-authority.md) | Observation capacity authority is policy-derived, not caller-asserted | proposed |
| [0059](0059-seed-uncertainty-and-provisionality-views.md) | Per-axis seed uncertainty by evidence tier; debt and provisionality are views over live confidence | proposed |
| [0060](0060-objective-mix-live-receding-horizon-microcycle.md) | The objective mix is recomputed live per request; the microcycle is the accounting horizon | proposed |
| [0061](0061-objective-target-share-function.md) | The objective target-share function: multiplicative score, diminishing aggregation, true share floors | proposed |
| [0062](0062-session-load-au-allocation-ledger.md) | Session-RPE load (AU) is the microcycle allocation currency, distinct from state-update dose | proposed |
| [0063](0063-session-commitment-and-issuance.md) | Session commitment is execution state; issuance is a user-visible event, not materialization | proposed |
| [0064](0064-receding-horizon-modality-assignment.md) | Objective mix reaches the plan through hierarchical receding-horizon assignment | proposed |
| [0065](0065-objective-progress-signal-evidence-contract.md) | Objective progress is a derived signal over P10 evidence, not a column | proposed |
| [0066](0066-strength-decline-hysteresis.md) | A single low benchmark must not durably regress max_strength (downward confirmation hysteresis) | proposed |
