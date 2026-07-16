---
status: accepted
date: 2026-07-05
---
# Shadow MPC planner: receding-horizon re-ranking of the prescriber's candidates

The prescriber is a **greedy one-step argmax** (`recommend_next_session`): each candidate is
scored by a linear `w·φ` over *current* state, and risk terms (tissue/deload) are shadow-only
nudges rather than part of a real objective. This ADR adds the proposal's Stage-6 **risk-aware
MPC** — but, like the shadow EKF (ADR-0041), as a **parallel shadow planner** that changes
nothing an athlete sees.

For each real prescription, the planner re-ranks *the prescriber's own candidate pool* by
horizon-lookahead and logs what MPC **would** choose versus what the greedy prescriber
**did** choose (`decision_impact="none_shadow_only"`, table `mpc_shadow_log`).

**How it works**

- **Candidate → dose**: a candidate carries only `type`/`focus`/`domain`/`duration_min`, so we
  infer a `(modality, intensity, scale)` intent and reuse the projection layer's
  `session_log_from_intent` → the real `calculate_stress_dose` (session-level, no DB exercise
  resolution). This synthesis helper was lifted into `app.engine.simulate` so both the
  projection service and the planner share it without a `logic → services` layering inversion.
- **Rollout**: apply today's candidate one step, then roll a **fixed goal-typical
  continuation** forward over the horizon (default 14 days) via the same
  `update_athlete_state` twin. The continuation is identical for every candidate, so score
  differences isolate *today's* decision.
- **Objective** `J = w_G·ΔG − λ_F·fatigue − λ_T·tissue − λ_I·injury − λ_D·deload − λ_U·tr(P)`:
  goal-weighted capacity gain against **convex** (squared) fatigue/tissue penalties, the
  `tissue_risk`/`deload_need` shadow modules at the horizon end, and the EKF belief's total
  uncertainty `tr(P)` (ADR-0041) as a conservatism term. Convex penalties are deliberate — a
  linear fatigue cost makes the planner trivially always pick the lightest option; squaring
  makes the *same* added load cheap when fresh and expensive when already loaded, so the policy
  is "train hard when fresh, back off when loaded."
- **Compare + log**: `mpc_choice = argmax J`, `greedy_choice = pool[0]`, record agreement +
  the per-candidate term breakdown. Best-effort, wired after decision telemetry in
  `prescribe_for_athlete`; a failure can never alter or block `rx`.

**Rejected / deferred**

- **No production behavior change** — the planner only logs until offline evidence (agreement
  patterns joined to outcomes) justifies promotion. (An `ENABLE_MPC_PRESCRIPTION` flag was
  originally described here as the promotion gate, but it was never wired — read by no code, it
  gated nothing — and was removed as a fictional control surface, AUD-C9 / 2026-07-16.
  Promotion to live is a separate feature mission: it must move the MPC calc before
  finalization, select the MPC candidate without committing the baseline first, and ship a
  shadow-vs-live replay harness + canary/rollback before any control flag returns.)
- **Stochastic/Monte-Carlo MPC** — v1 rolls a single *deterministic* trajectory per candidate;
  sampling `S`/`θ` from the posterior for `E[·]`/chance-constraints is deferred. The EKF belief
  enters only through `λ_U·tr(P)`, not full covariance propagation over the horizon.
- **Learned cost weights / contextual bandit** (Stage 8) — the `λ` are hand-set priors.
- **Per-day re-planning** — the continuation is fixed, not re-optimized each step.

**Guardrail:** the shadow MPC must never write to a prescription or production state, must be
best-effort, and the hard safety pre-filter (`_safety_candidates`) and the greedy selection
logic stay untouched. When safety fires, the pool is empty and the planner no-ops.
