---
status: accepted
date: 2026-06-20
---
# Eight capacity axes, engine to UI, no rollup

The capacity vector has **eight** axes — `aerobic, glycolytic, max_strength,
hypertrophy, power, skill, mobility, work_capacity` — and these are canonical across
the engine, the API, *and* the UI. `app/domain/vectors.py` already declares this set
as "the single source of truth for the mathematical model"; this ADR ratifies it and
extends it to the presentation layer.

We considered a 5-axis UI rollup (grouping the eight into display buckets) and rejected
it: a rollup adds an 8→5 mapping to maintain on every schema change and hides model
resolution the [PROJECT_AGENT_BRIEF](../../PROJECT_AGENT_BRIEF.md) deliberately wants.
We also rejected *collapsing the engine to 5*, which would discard fidelity and force an
`engine_state` migration. The denser UI is an accepted cost.

**Guardrail:** the API exposes all eight capacity axes; clients render all eight — do
not truncate the capacity vector at any layer.
