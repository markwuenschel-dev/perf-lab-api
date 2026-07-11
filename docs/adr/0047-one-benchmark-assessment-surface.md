---
status: accepted
date: 2026-07-07
delivery: delivered
---
# One benchmark assessment surface; no domain-specific seeders

> **Delivered — backend (P10 Slice 4, 2026-07-11).** `GET /v1/benchmarks/assessment-surface?mode=onramp|retest`
> (`assessment_surface_service`): the catalog filtered by the athlete's active domains
> (objectives' domains ∪ `primary_goal` → domain; empty ⇒ show-all so the measurement
> layer is never hidden), grouped by home domain, each card annotated with resolved
> `domain_lenses` (+source), the axes it measures, live-variance `confidence_status`
> (ADR-0059), last-observed time, eligibility, and a measurement-debt recommendation
> rank via `information_gain_proxy_v1` (`w_u·uncertainty + w_c·coverage − w_b·burden`,
> top-N per domain). Every submit is a `benchmark_observation` through the ADR-0058
> authority path (no domain-specific seeder). Per-domain `domain_lenses` curation is the
> reviewed `DOMAIN_LENS_CURATION` policy + `resolve_domain_lenses` (explicit_curated →
> curated_by_domain → home_domain_default), closing the ADR-0057 requirement.
> `/compute-metrics` marked `deprecated` — a stateless VO₂/zones calculator, never a
> seeding source of truth. Verified: ruff+pyright clean; 10 new tests (pure active-domain
> + utility helpers, DB domain-filtering/eligibility/recommendation/mode-guard) + a
> 48-test vocab/benchmark/seed/corruption sweep green; OpenAPI 42→43 paths + web types
> regenerated; web build green.
>
> **Deferred:** the running Field Test *screen* retirement + wiring the onramp/onboarding
> flow are the web + onboarding slices (P10 Slice 5/6). Full `measurement_debt`
> eligibility gating (safe ∧ protocol_valid ∧ equipment ∧ capable) + hysteresis
> (ADR-0059) layer onto this ranking as data allows.

The running Field Test was accidentally privileged: it had its own screen and its own
endpoint (`300m + 1.5mi → /compute-metrics → running-derived seed`), while the
37-benchmark / 7-domain catalog — the actual measurement layer
([PDR-0003](../pdr/0003-benchmarks-are-the-measurement-layer.md)) — went unused by the web.
Two measurement entry points with two state-seeding paths is exactly the parallel source of
truth PDR-0003 forbids, and it is why running still feels special. Keeping it would force a
new field-test UI per domain (running / strength / gymnastics / …).

We collapse the onboarding seed and the standalone Field Test into **one
`BenchmarkAssessmentSurface`**, invoked as `mode="onramp"` (onboarding — [PDR-0009](../pdr/0009-onboarding-benchmark-onramp.md))
or `mode="retest"` (ongoing). Mode is product framing; the data path is identical. The
surface filters the catalog by the athlete's active `domain_lenses` and, in onramp mode,
ranks a recommended few per domain. Every assessment writes a single **`benchmark_observation`**
(`source.type = onboarding_onramp | retest | imported | coach_entry`, raw inputs +
computed outputs + confidence + context); the backend owns the state update
([ADR-0035](0035-benchmark-seeded-initial-state.md)) — the frontend never seeds capacity
directly. The 300m+1.5mi run becomes the `run_vo2_field_test_300m_1p5mi` benchmark
definition; `/compute-metrics` is demoted to an internal calculator behind that definition,
never again a seeding source of truth. We rejected keeping the Field Test separate (two
seeders, running stays first-class, per-domain UI sprawl).

**Guardrail:** there are no domain-specific seeders — only benchmark observations.
Domain-specificity lives in benchmark *definitions* (`domain_lenses`, inputs, protocol,
`observation_mappings`, tags), never in separate screens or endpoints. One surface, many
definitions, domain-filtered views.
