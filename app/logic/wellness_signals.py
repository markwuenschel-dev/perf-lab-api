"""Canonical acute-wellness signal configuration.

The single source for the per-signal z-score parameters, shared by the readiness
combine rule (``app.services.readiness_service``) and the recovery-clearance shadow
telemetry (``app.logic.recovery_telemetry``). It previously lived as two independently
hand-maintained copies that could silently drift.

Per signal: ``(direction, default_baseline, norm)``
  direction         +1 = higher is better, -1 = lower is better
  default_baseline  anchor used when the athlete has no personal history yet
  norm              change (in signal units) that maps to one unit of deviation
"""
from __future__ import annotations

SIGNAL_CONFIG: dict[str, tuple[int, float, float]] = {
    "hrv_ms":        (+1, 60.0, 20.0),
    "sleep_hours":   (+1, 8.0,  2.0),
    "sleep_quality": (+1, 85.0, 15.0),
    "resting_hr":    (-1, 55.0, 10.0),
    "soreness":      (-1, 3.0,  3.0),   # 0–10, higher = worse
    "mood":          (+1, 6.0,  3.0),   # 0–10, higher = better
    "stress":        (-1, 4.0,  3.0),   # 0–10, higher = worse (P8; provisional, calibrate)
}
