---
status: proposed
date: 2026-07-07
---
# The app never blocks the user; the model self-limits by confidence

Onboarding was heading toward a gate — "measure a benchmark before you can use the
product." We reject that. The app must get a user to value quickly; benchmarks *increase
confidence*, they do not *unlock access*. So the only hard onboarding gate is **can we
prescribe safely at all** — the safety/feasibility basics (age / minor status,
contraindications & injury restrictions, available days, a primary objective, experience
level, equipment & environment). Everything that only improves *precision* (1RM, 5k, VO₂
field test, threshold test, technical grade, skill benchmarks, wearable sync) is
non-blocking. A user who measures nothing still enters, on a low-confidence experience-level
prior seed ([ADR-0035](../adr/0035-benchmark-seeded-initial-state.md)) shown as an
*estimated / provisional twin*, with unmeasured axes surfaced as **measurement debt** and
progressive, in-context prompts to sharpen them.

The counterpart is that the *model* limits itself: recommendation aggressiveness and strong
claims (race prediction, high-confidence tissue-risk, adaptation-rate) are gated by
confidence, not by whether the user finished a flow
([ADR-0048](../adr/0048-confidence-gates-recommendations.md)). We rejected blocking-onboarding
(fast to build, but holds the product hostage to a measurement the seed can stand in for) and
seeding nothing (an empty, useless twin).

**Guardrail:** never gate app access on a performance measurement. Block only on safety /
prescription feasibility. When evidence is thin, the model gets more conservative and
labels itself provisional — it does not lock the user out.
