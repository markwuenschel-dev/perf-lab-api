"""Model card for the hierarchical recovery-β personalization gate (ADR-0043)."""
from __future__ import annotations

MODEL_CARD = """\
HIERARCHICAL RECOVERY-β PERSONALIZATION -- MODEL CARD (personalization_recovery_v1)
==================================================================================

WHAT THIS IS
  The offline gate for per-athlete recovery-clearance β via empirical-Bayes PARTIAL
  POOLING (ADR-0043). It does not fit a production model -- it proves the estimator: on a
  synthetic population with KNOWN per-athlete β, does partial pooling predict held-out
  recovery better than both full pooling (population prior only) and no pooling (the
  athlete's own noisy fit)? Shadow-only; nothing here changes production.

THE MODEL
  Gaussian hierarchical model θ_i ~ N(μ_0, τ²), observed via data estimate β̂_i with
  sampling variance σ²/n_i. Posterior mean β_i = (1−w_i)·μ_i + w_i·β̂_i,
  w_i = τ²/(τ² + σ²/n_i); posterior variance P^θ_i = (1−w_i)·τ². μ_0/τ²/σ² are estimated
  across the population by method of moments. A new athlete (n_i→0) sits at the population
  prior; personalization grows with their own data.

GATE (seed-robust)
  Partial-pool held-out MAE must beat BOTH baselines by ≥ MIN_IMPROVEMENT. This holds on
  every seed -- the bias-variance win of shrinkage, strongest for sparse-data athletes.

GATED -- P^θ CALIBRATION
  mean tr(P^θ) / mean ‖β_i − β_true‖² should be ~1. Using the Gram-based sampling variance
  σ²·(ZᵀZ)⁻¹_jj for each coefficient (NOT the σ²/n approximation, which understates it and
  made P^θ ~2-4x overconfident), it lands ~1.0-1.2 across seeds and is now part of the gate.

PRODUCTION FEED
  The per-athlete resolver + shadow log lives in
  app.services.personalization_shadow_service: it partial-pools an athlete's β toward the
  Q2 population prior and logs population-vs-personalized clearance multipliers + n_i, w_i,
  tr(P^θ) to personalization_shadow_log. Shadow-only — never applied to production; the
  engine uses the global/population parameters. Promotion to per-athlete β in the live
  update path is a separate feature mission (see docs/adr/0043), not a runtime flag today.

REPRODUCE
  python -m app.ml.personalization.evaluate
"""
