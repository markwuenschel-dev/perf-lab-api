"""Shared helpers for the offline (shadow-only) ML pipelines under ``app/ml``.

Behavior-preserving extraction of the machinery duplicated across the eight offline
pipelines (q1_decrement, q2_recovery, q3_tissue, q6_deload, q8_scoring, q9_interference,
q10_confidence, dose_calibration). Every helper here is PARAMETERIZED by the constants
that differ per pipeline (group/order columns, feature lists, CV grids, artifact paths),
so each pipeline keeps a thin wrapper that binds ITS existing constants and the numeric
outputs stay byte-identical. No schema/naming unification happens here — only dedup.
"""
from __future__ import annotations
