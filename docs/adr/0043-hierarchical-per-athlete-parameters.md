---
status: accepted
date: 2026-07-05
---
# Hierarchical per-athlete parameters (θ_i): partial-pooled recovery β, shadow-only

Engine parameters are **global** — one `EngineParameters` for every athlete — and the override
system is population/namespace-keyed (its docstring: *"never per-athlete personalization"*). The
proposal's Stage 9/10 makes parameters **per-athlete** via hierarchical Bayesian partial
pooling, and adds the **parameter uncertainty `P^θ`** that the EKF arc (ADR-0041) deferred (it
did state uncertainty `P^S` only). This ADR builds that — starting with, and only with, the
recovery-clearance **β** — as a **shadow-only** capability.

**Why β first:** it is the one tractable parameter. Wellness/fatigue data is dense per athlete
(~29 recovery-days/athlete in Q2's data); capacity params (α, τ, δ) need benchmark density that
doesn't exist per-athlete yet. Q2 already builds the hook — it demeans clearance *per athlete*
to fit a **population** β, discarding the per-athlete offset — and partial pooling recovers
exactly that offset.

**The model** (empirical-Bayes, closed-form — no MCMC): `θ_i ~ N(μ_0, τ²)` observed via a data
estimate `β̂_i` with sampling variance `σ²/n_i`. Posterior mean `β_i = (1−w_i)·μ_i + w_i·β̂_i`,
`w_i = τ²/(τ² + σ²/n_i)`; posterior variance `P^θ_i = (1−w_i)·τ²`. `μ_0`/`τ²`/`σ²` are estimated
across the population by method of moments; a light covariate arm shifts `μ_i` by experience
level (a hand-set prior, not a fitted regression — age/sex/sport/injury covariates don't exist
on the profile). A new athlete (`n_i→0`) sits at the population prior; personalization grows
with their own data.

**Validated offline** (`python -m app.ml.personalization.evaluate`): on a synthetic population
with known per-athlete β, partial pooling beats **both** full pooling (population only) and no
pooling (per-athlete only) on held-out recovery MAE — the seed-robust shrinkage result, and the
gate. `P^θ` calibration is **reported, not gated**: it runs ~2-4× overconfident because the
multivariate coefficient sampling variance is `σ²·(ZᵀZ)⁻¹`, which `σ²/n` understates — so `P^θ`
is a coarse ordinal conservatism signal here, and a Gram-based correction is the documented
follow-up.

**Shadow wiring:** on wellness ingest, `personalization_shadow_service` builds the athlete's
bounded-window recovery frame, partial-pools their β toward the Q2 population prior, and logs
population-vs-personalized clearance multipliers + `n_i`, `w_i`, `tr(P^θ)` to
`personalization_shadow_log`. It computes multipliers via `recovery_telemetry` — never calling
`update_athlete_state` — exactly as the Q2 recovery-shadow service does.

**Rejected / deferred:** only β is personalized (α/τ/δ, interference Ψ wait for per-athlete
benchmark density; the estimator generalizes); no production change (gated behind
`ENABLE_PERSONALIZED_RECOVERY = False`); no full covariate regression or MCMC; the Gram-based
`P^θ` correction; `apply_parameter_overrides` stays population-only and `update_athlete_state`
is untouched.

**Guardrail:** personalization must never write to a prescription or production state, must be
best-effort, and a sparse athlete must resolve to the population prior (`w_i=0`) — no
personalization is applied without the data to support it.
