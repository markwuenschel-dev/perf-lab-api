# Performance Lab Design Decisions

> **Moved.** Architecture decisions now live as individual records in
> [`docs/adr/`](adr/) and product decisions in [`docs/pdr/`](pdr/). Each former
> section of this file became an ADR (the old "Rule" line is the record's
> **Guardrail**):

The 22 decisions previously listed here are now:

- **State & dose:** [ADR-0001 persist state history](adr/0001-persist-state-history.md) ·
  [0002 logs separate from state](adr/0002-separate-logs-from-state.md) ·
  [0003 explicit dose layer](adr/0003-explicit-dose-layer.md) ·
  [0004 preview vs mutation](adr/0004-separate-preview-and-mutation.md) ·
  [0005 multi-dimensional fatigue](adr/0005-multidimensional-fatigue.md) ·
  [0006 separate capacity/fatigue/tissue](adr/0006-separate-capacity-fatigue-tissue.md) ·
  [0007 legacy scalar mirrors](adr/0007-legacy-scalar-mirrors.md)
- **Weak points & planning:** [0008 weak points as signals](adr/0008-weak-points-as-signals.md) ·
  [0009 bias not hijack](adr/0009-weak-points-bias-not-hijack.md) ·
  [0010 planning vs adaptation](adr/0010-separate-planning-and-adaptation.md) ·
  [0011 lazy session content](adr/0011-lazy-planned-session-content.md) ·
  [0012 link workouts to planned sessions](adr/0012-link-workouts-to-planned-sessions.md)
- **Measurement:** [0013 benchmarks as measurement layer](adr/0013-benchmarks-as-measurement-layer.md) ·
  [0014 KPIs as snapshots](adr/0014-kpis-as-snapshots.md) ·
  [0015 mappings before EKF](adr/0015-mappings-before-ekf.md) ·
  [0016 exercise metadata layer](adr/0016-exercise-metadata-layer.md)
- **Platform & conventions:** [0017 auth outside /v1](adr/0017-auth-outside-v1.md) ·
  [0018 Alembic-only schema](adr/0018-alembic-only-schema.md) ·
  [0019 thin deprecated modules](adr/0019-thin-deprecated-modules.md) ·
  [0020 frontend manual mirrors](adr/0020-frontend-types-manual-mirror.md) *(superseded by [0025](adr/0025-generate-ts-types-from-openapi.md))* ·
  [0021 frontend mirrors backend domain](adr/0021-frontend-mirrors-backend-domain.md) ·
  [0022 legibility over cleverness](adr/0022-legibility-over-cleverness.md)

See [`docs/adr/README.md`](adr/README.md) for the format and the full index, and
[`docs/pdr/README.md`](pdr/README.md) for product decisions. New decisions go in those
directories, not here.
