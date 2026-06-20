---
status: accepted
date: 2026-06-20
---
# Populate planned-session content lazily

A `PlannedSession` stores the slot first; its `prescribed_content` is written only when
today's session is opened or a matching next-session prescription is generated. The
exact content should use fresh `S(t)`, weak points, KPIs, and recent workout context
rather than stale values computed at plan-creation time.

**Guardrail:** do not precompute exact session content early unless the product
explicitly wants a static plan.
