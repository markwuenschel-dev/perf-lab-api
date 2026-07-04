---
status: proposed
date: 2026-07-04
---
# The macrocycle is a thin container, not a materialized plan

Blocks ([`MesocycleBlock`](../../app/models/mesocycle.py)) had no parent, so "week X of
Y" could only mean "week within the current block" — there was no cross-block horizon
pointed at an [Objective](../pdr/0004-objectives-first-class.md). We add a `Macrocycle`: a thin
row (`user_id`, `objective_id` anchor, `start_date`, `status`) plus a **nullable**
`program_id` FK on `mesocycle_blocks`. Cross-block progress is *computed* — `X =
weeks_since(macrocycle.start_date)`, `Y = weeks_to(anchor_objective.target_date)` — never
stored; blocks are still generated and adapted one at a time by the engine. We rejected a
**fully materialized macrocycle** (persist the whole accumulation→peak→taper block sequence
backward from the objective date via the inert `PlanTemplate`): it re-introduces the
multi-week *rail* [PDR-0008](../pdr/0008-plan-is-a-seed-not-a-rail.md) forbids, goes stale
the moment readiness forces the engine to adapt, and is the most expensive to reverse. We
also rejected a **pure derived view** (no table): it leaves the Objective→blocks
association recomputed ad hoc every prescribe (as `active_objective_signals` does today) and
gives the macrocycle no identity to hang status or history on.

Named `Macrocycle` — not `Program` — to avoid colliding with the unrelated
[`ProgramTemplate`](../../app/schemas/program_template.py) coaching-decomposition schema.
The anchor is an explicit `objective_id` FK (defaulting at creation to the top-priority
active objective), so the [ADR-0038](0038-canonical-domain-taxonomy.md) domain and taper
signals a block already reads become a stored association instead of a per-prescribe scan.

**Guardrail:** the macrocycle stores only its anchor and start; the block sequence inside
it is never persisted ahead of time. "Week X of Y" and any forward shape are derived from
`start_date` + the anchor Objective's `target_date` + the blocks that already exist — a
horizon the engine fills as it goes, not a script it must follow.
