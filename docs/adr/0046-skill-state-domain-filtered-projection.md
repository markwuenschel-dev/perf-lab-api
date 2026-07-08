---
status: proposed
date: 2026-07-07
---
# Skill state is a domain-filtered projection over measured evidence

The web "Skill state" card was fiction: a hardcoded `SKILL_DEFS` list (running economy,
cadence, hill technique, descending, pacing, fueling) with values faked by a day-index
ramp, disconnected from the backend. The backend has no per-domain skill taxonomy — only a
whole-body `capacity.skill` scalar and a strength-keyed `skill_state` dict. We must define
skill state, and the temptation is a rich per-domain technique taxonomy
(`skill.running.cadence`, `skill.strength.bar_path`, …). We reject that: it is a new
parallel vocabulary to define/measure/validate/map, and it reintroduces the exact
named-values-with-no-evidence problem we found in `sim.ts` — violating
[ADR-0038](0038-canonical-domain-taxonomy.md) and [PDR-0002](../pdr/0002-domain-as-lens-over-one-body.md).

**Skill state is a *view*, not an ontology** — a domain-filtered projection over unified
state, evidence-backed or explicitly unknown:

- **Sources.** `capacity.skill` (whole-body motor/skill-learning capacity, the anchor),
  the per-movement `skill_state` dict, skill/technique **benchmark observations**, and
  **weak-point tags** for the athlete's active domains. The canonical state
  `S=(X,F,T,C)` is unchanged; skill is projected out of it, never a separate store.
- **`skill_state` graduates to an open movement-keyed proficiency map** — seeded for the
  big lifts at onboarding, then extended by benchmark observations (`wl_technical_grade`
  writes `clean`/`snatch`; `gym_transition_quality` writes `ring_muscle_up`). It is a
  lens input, not domain-isolated — a pull-up feeds both strength and gymnastics views.
- **Benchmark/weak-point definitions gain view metadata**, not new state: `domain_lenses`
  (which domain cards surface it; defaults to the canonical `domain`),
  `movement_skill_mappings` (which `skill_state` movements an observation updates), and
  `assessable_skill_tags` + `measurement_protocol` (the label and the way to measure it).
- **Confidence reuses the per-axis confidence scalar
  ([ADR-0036](0036-per-axis-confidence-scalar.md))** plus evidence recency — no parallel
  confidence system.
- **The honesty ladder.** `value = null` means unknown; `0` never does. *Future idea →
  no UI item · assessment path exists → "not yet measured" · observation exists →
  measured.* An item appears as "not yet measured" **only if** a benchmark def or
  assessable weak-point tag with a real `measurement_protocol` exists for it in an active
  domain. A technique that matters but has no protocol (hill technique) is a prompt to
  define a protocol, or lives in the product backlog — never a protocol-less placeholder
  in state or UI. Weak-point tags with partial evidence show as "needs assessment"; they
  are hypotheses/routing metadata, never canonical state axes.
- **`sim.ts` `SKILL_DEFS` + the ramp are removed** (or quarantined behind an explicit,
  visibly-fake demo mode — never mixed with production state).

The constraint engine keeps gating candidates on `skill_state`; when a required skill is
unmeasured it selects an assessment or a low-risk progression rather than inventing a score.

**Guardrail:** skill is a projection over measured evidence, not a taxonomy. Never display
a skill value without an observation behind it, and never list a "not yet measured" skill
that has no assessment path. Enrich benchmark/weak-point definitions with view metadata;
do not add per-domain skill state axes.
