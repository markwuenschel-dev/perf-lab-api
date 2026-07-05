"""Offline validation of hierarchical per-athlete recovery-β personalization (ADR-0043).

Mirrors the Q2 pipeline shape but proves the *partial-pooling* estimator rather than fitting a
population prior: on a synthetic population with known per-athlete β, does partial pooling
predict held-out recovery better than both full pooling (population only) and no pooling
(per-athlete only), and is the posterior parameter uncertainty P^θ calibrated?
"""
from __future__ import annotations
