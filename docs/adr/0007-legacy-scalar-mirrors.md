---
status: accepted
date: 2026-06-20
---
# Keep legacy scalar mirrors aligned with engine vectors

Each `AthleteState` persists both the decomposed `engine_state` JSONB and the legacy
scalar columns, with bridge helpers keeping them aligned, so old clients keep working
while the engine evolves toward richer vector state. The trade-off is duplication: the
bridge layer must stay disciplined and derive the scalars from the unified vector
rather than letting the two drift apart.

**Guardrail:** when writing `AthleteState`, derive legacy columns from the unified
vector — never hand-set them independently.
