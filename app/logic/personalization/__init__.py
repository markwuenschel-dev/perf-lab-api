"""Hierarchical per-athlete parameter personalization (θ_i), starting with recovery β.

Closed-form empirical-Bayes **partial pooling**: an athlete's parameter is a precision-weighted
blend of a population prior and their own data estimate, so a new athlete starts at the
population value and personalizes only as their own data accumulates. Also yields the
per-parameter posterior uncertainty ``P^θ`` (the parameter-uncertainty the EKF arc, ADR-0041,
deferred). Pure / DB-free. See ADR-0043.

v1 personalizes only the recovery-clearance β (the one parameter with dense enough per-athlete
data); the estimator is written to generalize to other scalar parameters.
"""
from __future__ import annotations
