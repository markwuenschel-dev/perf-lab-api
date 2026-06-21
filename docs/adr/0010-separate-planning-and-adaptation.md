---
status: accepted
date: 2026-06-20
---
# Keep planning and adaptation separate

`MesocycleBlock` and `PlannedSession` carry strategic structure (direction), while the
prescriber uses current `S(t)` to fill daily content (dosage and constraints). This
protects against two bad extremes: rigid plans that ignore readiness, and reactive
day-by-day generators that lose long-term coherence.

**Guardrail:** a block must not eliminate adaptive state-based prescription, and daily
adaptation must not erase block identity.
