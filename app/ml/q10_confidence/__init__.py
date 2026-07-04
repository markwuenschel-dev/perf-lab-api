"""Q10 confidence-calibration offline ML pipeline (Rail 1, shadow-only).

Calibrates the engine's per-axis capacity process-noise
(``EngineParameters.confidence_process_noise_per_day``, ADR-0036) so the model's
PREDICTED capacity-axis variance tracks the OBSERVED squared residual between
successive benchmark observations. Runnable/testable without Postgres via a
synthetic fixture that plants a known process-noise and recovers it.

Everything here is pandas/numpy (kept out of the strict-pyright feature-builder
gate); the DB-backed production feed is
``app.analysis.feature_builders.confidence_calibration_features``.
"""
from __future__ import annotations

from app.ml.q10_confidence.build_training_frame import (
    AXES,
    FORBIDDEN_FEATURES,
    build_frame,
    grouped_split,
    synthesize_observations,
)

__all__ = [
    "AXES",
    "FORBIDDEN_FEATURES",
    "build_frame",
    "grouped_split",
    "synthesize_observations",
]
