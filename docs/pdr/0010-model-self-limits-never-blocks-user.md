---
status: accepted
date: 2026-07-07
delivery: partial
---
# The app never blocks the user; the model self-limits by confidence

> **Delivered — backend state machine (P10 Slice 5, 2026-07-11).**
> `app/logic/onboarding_state.py` (pure): the non-blocking status machine
> (not_started → in_progress → completed), `required_basics_missing` (the ONLY hard
> gate — primary objective + declared equipment/environment + available days; precision
> inputs are never listed), `can_prescribe`, and the completion reasons (finished /
> done_for_now / skipped). `onboarding_service` surfaces `GET /v1/onboarding/state`
> (status, `can_prescribe` / `missing_basics`, a provisional twin summary derived from
> live variance + the ADR-0059 seed rollup, and progressive measurement-debt prompts
> from the ADR-0047 assessment surface) and `POST /v1/onboarding/complete` (leave any
> time, records the reason — never locks the user out). `/onboard` advances the machine
> to in_progress on basics; the experience-prior fallback seed already ships (ADR-0035 +
> Slice 3 tiers it as `experience_prior`, shown provisional). Verified: ruff+pyright
> clean; 12 new tests (pure gate/transition/reason + DB state, provisional-but-usable
> twin, non-blocking exit, bad-reason guard); OpenAPI 43→45 paths + web types
> regenerated; web build green.
>
> **Deferred:** the onboarding *UI* (skippable steps, per-benchmark "do this later",
> the provisional-twin display) is P10 Slice 6; age/minor + contraindication profile
> fields join `required_basics_missing` when modeled; confidence-gated recommendation
> aggressiveness is ADR-0048 (P13).

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
