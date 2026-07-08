---
status: proposed
date: 2026-07-07
---
# Confidence gates recommendations — continuously in the engine, discretely on claims

[PDR-0010](../pdr/0010-model-self-limits-never-blocks-user.md) lets an unmeasured user into
the app on a low-confidence seed. For that to be safe *and* honest, the per-axis confidence
scalar ([ADR-0036](0036-per-axis-confidence-scalar.md)) has to stop being merely *displayed*
and become something the prescriber *acts on*. We split the mechanism by the shape of the
output:

- **Continuous where the quantity is continuous.** Per-axis confidence tightens the ceiling
  on progression rate and intensity jumps — low confidence → conservative, high → the full
  [ADR-0029](0029-periodization-intent-envelope.md) envelope. A squat with a low-confidence
  e1RM gets conservative loading even when the point estimate is high. This composes with the
  relative frame of [ADR-0032](0032-relative-state-math-benchmark-anchored.md).
- **Threshold-gated where the output is a discrete claim.** Below a confidence threshold,
  strong claims are suppressed at the surface: precise race prediction, high-confidence
  tissue-risk statements, strong adaptation-rate claims. They return as confidence rises.

Confidence-gating is a **first-class prescriber input, distinct from the safety-override
system.** Safety means "don't hurt you" (injury / tissue, a hard override); confidence means
"we don't know you well enough to push" (an epistemic ceiling). They both cap aggressiveness
but for different reasons and must stay separate — a low-confidence *but safe* athlete is
still trained, conservatively, and nudged toward assessments, not blocked. We rejected pure
discrete tiers (throws away the continuous scalar we already compute) and merging it into
safety (conflates "unsafe" with "unmeasured").

**Guardrail:** confidence scales the engine's aggressiveness ceiling continuously and gates
discrete claims by threshold — it never blocks training outright. Keep it separate from
safety overrides; never derive one from the other.
