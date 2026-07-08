---
status: proposed
date: 2026-07-07
---
# The user owns intent and structure; the engine owns safety and execution

Objectives auto-compute the *efficient* path to an outcome ([ADR-0050](0050-objectives-drive-training-emphasis.md)),
but athletes don't only optimize for outcomes — they optimize for enjoyment, identity,
adherence, seasonality, novelty, and motivation, and they want to shape a cycle even when it
isn't the fastest route to a goal. Expressing every such wish as an objective or a floor is
too narrow: "run this block as hypertrophy though peak-strength is more efficient," "stay in
base four more weeks," "no barbell squats this block" don't map cleanly to an objective.

We adopt an explicit **authority stack**: (1) **safety** constraints are absolute; (2) a
**user hard override** is authoritative; (3) **objectives / priorities / floors** define
desired outcomes; (4) the **optimizer** chooses the best plan *inside* those; (5) a
**tradeoff explanation** is mandatory. A `PlanningOverride` (scope session/week/block/range;
type pin-modality-mix / pin-goal / pin-phase / min-or-max-frequency / include- or
exclude-modality / movement-preference) carries an `authority` of **`hard_user_override`**
(honored unless unsafe) or **`soft_user_preference`** (tradeable by the optimizer). The
planner pipeline is: objective blend → apply floors → apply overrides → check
safety/confidence gates → generate candidates → optimize *within the declared structure* →
explain the tradeoff. The optimizer stays useful — it just optimizes inside the user's box
and **never silently recomputes toward efficiency.** When an override costs objective
progress, the engine surfaces the cost ("this hypertrophy block delays your squat objective
~3 weeks — proceed?") — informed, not hidden, never "wrong choice."

This applies [PDR-0008](../pdr/0008-plan-is-a-seed-not-a-rail.md) (the auto plan is a seed the
athlete reshapes) and [PDR-0010](../pdr/0010-model-self-limits-never-blocks-user.md) (the
model informs, it does not overrule the user) to periodization. We rejected steering only
through objectives/floors (too narrow), honoring overrides without explaining cost (hides the
tradeoff), and silently correcting a pinned structure toward the efficient path (violates
trust). Overrides remain bounded by safety and confidence gates — a pinned frequency that
elevates tissue risk is modified or refused with an explanation, never obeyed into harm.

**Guardrail:** the user owns intent and structure; the engine owns safety, feasibility, and
execution quality. A hard override is violable only by a safety gate, and any deviation from
the efficient path must surface its objective cost — the optimizer never silently overrides
declared intent.
