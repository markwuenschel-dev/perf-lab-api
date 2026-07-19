"""AUD-C8/C24: immutable wellness input + versioned content hash for the shadow-EKF claim.

Two jobs:

- **Immutable hand-off** (C24): the shadow-EKF writer receives a frozen snapshot, never a live
  ``WellnessSample`` ORM instance. An ORM instance shared across shadow stages can be expired by
  an earlier shadow's rollback, silently corrupting a later shadow's read (classified
  production-real in the 2026-07-18 shadow-cascade diagnostic).

- **Content hash** (C8): the idempotency key's content fingerprint. Only ``soreness`` currently
  drives ``build_wellness_observation``, so it is the sole EKF-consumed field the hash covers —
  a correction to an unrelated wellness field (e.g. ``hrv``) must NOT be misread as an EKF
  correction. If the EKF consumes more wellness signals later, add them here and bump
  ``HASH_POLICY_VERSION``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

# Folded into every hash so an encoding/normalization change is explicit, never a silent mass
# re-classification of retries as corrections. FROZEN: bumping it requires a rehash migration.
HASH_POLICY_VERSION = "wellness_ekf_hash_v1"


@dataclass(frozen=True)
class WellnessMeasurement:
    """The normalized wellness content that affects EKF assimilation (soreness only, today)."""

    soreness: float | None


@dataclass(frozen=True)
class WellnessShadowInput:
    """Immutable hand-off to the shadow-EKF writer — never a live ``WellnessSample`` instance."""

    user_id: int
    wellness_sample_id: int
    measurement: WellnessMeasurement
    content_hash: str


def wellness_content_hash(measurement: WellnessMeasurement) -> str:
    """Deterministic, versioned SHA-256 of the EKF-consumed measurement content.

    Canonical serialization: stable key order, explicit null, fixed numeric rounding, UTF-8,
    with the policy version folded in. 64-hex chars (matches ``ekf_shadow_log`` column widths).
    """
    soreness = measurement.soreness
    payload = {
        "policy": HASH_POLICY_VERSION,
        "soreness": None if soreness is None else round(float(soreness), 4),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_wellness_shadow_input(
    user_id: int, wellness_sample_id: int, soreness: float | None
) -> WellnessShadowInput:
    """Snapshot a wellness observation into the immutable EKF input. Call while the source
    ``WellnessSample`` is still valid (before any shadow writer can roll the session back)."""
    measurement = WellnessMeasurement(soreness=soreness)
    return WellnessShadowInput(
        user_id=user_id,
        wellness_sample_id=wellness_sample_id,
        measurement=measurement,
        content_hash=wellness_content_hash(measurement),
    )
