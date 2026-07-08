---
status: accepted
date: 2026-07-08
---
# Readiness confidence is a report-only reliability object; gating waits for P13

The morning check-in (P8) makes readiness honest about *how well-supported* today's estimate
is, not just *how ready* the athlete appears. We separate two questions the old single number
conflated:

- `readiness.score` — how ready does the athlete appear today?
- `readiness.confidence` — how much evidence supports that score?

`ReadinessScore` gains `score` (renamed from `readiness`), a coarse `band`, and a structured
`confidence` object: `{score (0–1), band, status, reasons[], signal_summary, recommendation_gate}`.
Confidence is an **evidence-coverage** blend (not a posterior):
`0.35·load + 0.35·wellness-coverage + 0.20·freshness + 0.10·baseline-maturity`. When the athlete
tracks no coverage signals the wellness term is *not-applicable* (dropped, the rest renormalized) —
never treated as full coverage, so "track nothing" cannot manufacture high confidence.

## The bright line: score can nudge, confidence cannot gate

**In P8 the prescriber may consume `readiness.score`** through the existing bounded scoring channel
(`recommend_next_session(..., readiness_override=...)` → `score_template`, bounded by
`WELLNESS_WEIGHT = ±0.15`, ADR-0026). A bad night lowers the *visible* score and the plan shifts
because the number you can see shifted — transparent, score-driven adaptation, not hidden authority.
The axis-keyed `_readiness_redirect` stays modeled-only (acute wellness has no honest per-axis map).

**In P8 the prescriber must NOT consume `confidence` or `recommendation_gate.max_recommendation_authority`.**
The gate is computed, displayed, and logged with `enforced=false`; missingness/coverage never caps
intensity, blocks candidates, or forces a conservative mode. Enforcing confidence as authority is
planner-wide policy (candidate caps, claim suppression, fallback behavior, calibration) and belongs
to **P13** ([ADR-0048](0048-confidence-gates-recommendations.md)). Pulling it into a check-in PR would
smuggle planner behavior into an observability change.

Gate messages therefore describe the *data* ("Readiness is calculated without HRV today, so
confidence is lower"), never a recommendation effect the prescriber does not apply. `assessment_prompt_only`
degrades to advisory copy — it never blocks ([PDR-0010](../pdr/0010-model-self-limits-never-blocks-user.md)).

## Shadow logging

Every prescription logs a `readiness_audit` (in the decision-telemetry `block_context_json`):
`readiness_score_used_by_prescriber`, `readiness_score_adjustment`, `recommendation_gate{authority,
enforced:false}`, `confidence_used_by_prescriber:false`, and the signal buckets — so P13 can answer
"what would confidence-gating have changed?" from real history before it turns the gate on.

**Guardrail:** the readiness *score* may move the plan (transparently, bounded); the readiness
*confidence* may not (yet). `enforced` stays `false` until P13.
