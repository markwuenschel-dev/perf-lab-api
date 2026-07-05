"""Shadow Extended Kalman Filter over the full athlete state S = (X, F, T).

This package is a *parallel, shadow-only* state estimator. It generalizes the
production scalar per-axis capacity variance (``CapacityConfidence``, ADR-0036) into a
full joint covariance ``P`` over the 22-dim state, with an EKF predict (covariance
propagation through the deterministic twin) and update (joint measurement correction).

Key design choices (see ADR-0041):

- The EKF's transition model *is* ``update_athlete_state`` — linearized by finite
  differences around the real function — so the shadow estimator can never drift from
  production dynamics. It adds a covariance channel on top of the same mean trajectory.
- All EKF math runs in **normalized per-axis space** (each axis divided by its scale:
  capacity by its ceiling, fatigue/tissue by 100), matching the relative [0, 1]
  residual semantics the production scalar Kalman already uses (ADR-0034/0036). This
  makes benchmark observation models trivial (``H = e_key``, ``R`` = effective variance)
  and consistent with the existing path.
- Nothing here touches production state or prescriptions. It is written to
  ``ekf_shadow_log`` and validated offline.

Out of scope (v1): parameter uncertainty ``P^theta`` / hierarchical ``theta_i``, and the
Banister signal reservoir ``s_struct_signal`` (carried in the transition context but not
part of the covariance).
"""
from __future__ import annotations
