---
status: accepted
date: 2026-06-20
---
# Store derived KPIs as snapshots

Derived metrics are computed from observations and stored as `DerivedMetricSnapshot`
rows. Derived KPI values change as observations or formulas change, so snapshotting
gives an audit trail and dashboard history rather than recomputing transient,
UI-only numbers that can't be reconstructed later.

**Guardrail:** do not hide all KPI computation as transient UI-only logic.
