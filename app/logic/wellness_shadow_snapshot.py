"""AUD-C24: immutable current-wellness snapshot handed to the display-telemetry shadows.

The recovery and personalization shadow writers used to receive the live ``WellnessSample``
ORM instance. On a partial shadow failure an earlier writer's rollback expires that instance,
and a later writer reading it hits ``MissingGreenlet`` and silently drops its telemetry row
(classified production-real in the 2026-07-18 shadow-cascade diagnostic). This frozen snapshot
is the boundary: it carries exactly the five current-observation values those writers consume,
constructed while the sample is still valid, so no shadow reads a live ORM entity.

Distinct from the EKF's ``WellnessShadowInput`` (source identity + content hash + model-version
idempotency): those are the concerns of identified numerical assimilation, not display
telemetry, and are deliberately kept out of this type (see AUD-C24 decision).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.wellness import WellnessSample


@dataclass(frozen=True, slots=True)
class WellnessTelemetrySnapshot:
    """The exact current-wellness values the recovery + personalization telemetry consume.

    ``None`` means *not provided* and is preserved as such — never normalized to a value.
    A legitimate ``0.0`` must not be mistaken for missing, and missing must not become healthy
    telemetry. Fields keep their source domain type (``float | None``).
    """

    sleep_hours: float | None
    hrv_ms: float | None
    resting_hr: float | None
    soreness: float | None
    mood: float | None

    @classmethod
    def from_sample(cls, sample: WellnessSample) -> WellnessTelemetrySnapshot:
        """Snapshot a finalized ``WellnessSample`` at the boundary. The ORM type is allowed
        here (construction only); it is forbidden beyond this module in the shadow writers."""
        return cls(
            sleep_hours=sample.sleep_hours,
            hrv_ms=sample.hrv_ms,
            resting_hr=sample.resting_hr,
            soreness=sample.soreness,
            mood=sample.mood,
        )
