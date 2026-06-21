---
status: proposed
date: 2026-06-21
---
# The training plan is a seed, not a rail

Performance Lab authors a starting plan **once**, at account creation, then adapts it
from logged data — it does not hand the athlete a fixed multi-week program to execute.
Periodization structure (phases, progression) still exists, but as an *envelope the
engine bends to readiness*, not a contract. We rejected both extremes: classic fixed
programs (rigid, ignore readiness — the failure mode of every PDF plan) and pure
day-by-day autoregulation (coherent for one session, incoherent across a cycle).
Autoregulation is **primary**; the template supplies long-horizon shape.

Implemented by [ADR-0029](../adr/0029-periodization-intent-envelope.md); builds on
[ADR-0010](../adr/0010-separate-planning-and-adaptation.md).

**Guardrail:** default to inferring plan structure from data. Treat any flow that asks
the athlete to hand-build or hand-maintain a multi-week plan as a fallback, not the
primary path — the seed plus the data should be enough.
