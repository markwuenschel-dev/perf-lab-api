---
status: proposed
date: 2026-07-07
---
# Missing wellness signals are gaps, not imputed

The web check-in was a client-side simulation: every field a slider that always held a
value, no way to say "I don't track this" or "I don't know today," and readiness faked
locally against `sim.ts` coefficients — divergent from the one backend number
([PDR-0005](../pdr/0005-one-backend-owned-readiness-number.md)). The backend already treats
every wellness field as optional and does no imputation. We make the product honest about
missingness rather than papering over it.

Each signal has three states: **untracked** (a persistent per-user preference — "I don't own
an HRV device" — hidden, never expected, small/no penalty), **unknown today** (tracked but
absent — a visible gap, readiness computed without it, confidence reduced), and **provided**
(measured, normal). Readiness uses *only measured signals* plus modeled fatigue and recent
dose; **there is no default baseline carry-forward.** A tracked-but-missing signal is often
missing *not at random* — a skipped check-in, an unworn watch, a disrupted night all
correlate with a worse state, so substituting the 28-day baseline would manufacture false
normality on exactly the days it is most harmful. The baseline is **display-only
interpretation** ("your usual HRV is ~62; none recorded today"), never a silent input.
Carry-forward is deferred to an explicit *estimated* promotion level (reduced weight,
labeled, confidence-gated, and only if calibration shows it helps) — not shipped first.

`CheckinModal → ingestWellness`, `ReadinessCard → getReadiness`; the local readiness sim is
retired. We rejected default baseline carry-forward (manufactures false normality on
non-random missingness) and keeping the client-side readiness formula (violates PDR-0005).

**Guardrail:** never feed a client-side, carried-forward, or silently-imputed value into
readiness. Missing lowers confidence; it is never filled to look measured. The only readiness
inputs are measured wellness, modeled fatigue, and dose.
