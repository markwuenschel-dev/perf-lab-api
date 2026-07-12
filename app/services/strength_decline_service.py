"""Downward strength-decline state machine — service layer (INT-02, ADR-0066).

Sits on the benchmark ingestion path. A protocol-valid ``bidirectional_update``
observation whose ``max_strength`` residual is *materially* downward does NOT rewrite
canonical capacity on first evidence: the axis is held at its prior and a decline
candidate is opened (:func:`evaluate_first_observation`). Durable regression happens
only after independent corroboration, via a bounded estimator (see T6).

Design split: :func:`assess_decline` is **pure** (no DB, unit-tested directly) and
owns the provisional v1 variance model layered on the calibrated
:mod:`app.logic.strength_decline_policy` threshold math; the ``async`` functions are a
thin persistence layer.

Provisional v1 (``strength_decline_policy_v1``, ``synthetic_and_expert_prior`` — NOT
calibrated; shadow before retuning):
- decision space is raw e1RM units; the prior is the best *currently valid*
  demonstrated e1RM (the watermark), not the latent axis;
- the observation is treated as noisier under fatigue (``obs_std`` scales with mean
  fatigue), so a fatigued low test needs a larger drop to count as material — directly
  answering "a single low result can reflect fatigue" (Grgic et al., 2020).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic import observation_authority as oa
from app.logic import strength_decline_policy as policy
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.strength_decline_candidate import (
    STATUS_ACTIVE,
    STATUS_SAFETY_ROUTED,
    StrengthDeclineCandidate,
)
from app.schemas.engine_vectors import FatigueState
from app.schemas.state import UnifiedStateVector

logger = logging.getLogger(__name__)

DECLINE_AXIS = "max_strength"
# Provisional (not calibrated): mean fatigue of 1.0 doubles observation noise.
FATIGUE_NOISE_SCALE = 1.0
# Provisional window in which independent corroboration must arrive.
CONFIRMATION_WINDOW_DAYS = 90

# Applied-effect / transition-status stamps recorded on the observation.
APPLIED_NONE = oa.CE_NONE
APPLIED_BIDIRECTIONAL = oa.CE_BIDIRECTIONAL_UPDATE
TS_NO_MATERIAL_DECLINE = "no_material_decline"
TS_DECLINE_CANDIDATE = "decline_candidate"
TS_SAFETY_ROUTED = "safety_routed"


# --------------------------------------------------------------------------- #
# Pure decision core
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class DeclineAssessment:
    classification: str
    prior_mean: float
    observed_value: float
    delta_down: float
    threshold: policy.ThresholdResult
    prior_variance: float
    observation_variance: float
    mean_fatigue: float

    @property
    def is_material(self) -> bool:
        return self.classification in (policy.DECLINE_CANDIDATE, policy.SEVERE_DECLINE)


def _sem_equivalent(measurement_error_value: float, sem: float | None) -> float:
    """Back out an SEM from the resolved measurement error when none is stated:
    inverse of ``MDC95 = 1.96·√2·SEM``."""
    if sem is not None:
        return sem
    return measurement_error_value / (1.96 * math.sqrt(2.0))


def assess_decline(
    *,
    prior_mean: float,
    observed_value: float,
    error: policy.MeasurementError | None,
    mean_fatigue: float,
    z_down: float = policy.DEFAULT_Z_DOWN,
) -> DeclineAssessment:
    """Pure v1 assessment of a downward observation against the prior watermark.

    Layers a fatigue-aware observation-variance model on the policy threshold, then
    classifies via :func:`policy.classify_transition`. No I/O.
    """
    me = policy.resolve_measurement_error(error, prior_mean)
    sem_eq = _sem_equivalent(me.value, error.sem if error is not None else None)
    fatigue_factor = FATIGUE_NOISE_SCALE * max(0.0, min(1.0, mean_fatigue))
    observation_variance = (sem_eq * (1.0 + fatigue_factor)) ** 2
    prior_variance = 0.0  # v1: a demonstrated watermark is treated as confident
    thr = policy.material_decline_threshold(
        prior_mean=prior_mean,
        prior_variance=prior_variance,
        observation_variance=observation_variance,
        error=error,
        z_down=z_down,
    )
    delta = policy.downward_residual(prior_mean, observed_value)
    classification = policy.classify_transition(delta, thr)
    return DeclineAssessment(
        classification=classification,
        prior_mean=prior_mean,
        observed_value=observed_value,
        delta_down=delta,
        threshold=thr,
        prior_variance=prior_variance,
        observation_variance=observation_variance,
        mean_fatigue=mean_fatigue,
    )


@dataclass
class DeclineOutcome:
    """What the ingestion caller should do with a bidirectional downward observation."""

    hold_axis: bool
    applied_capacity_effect: str
    decline_transition_status: str
    candidate: StrengthDeclineCandidate | None


def hold_axis_at_prior(
    prior: UnifiedStateVector, updated: UnifiedStateVector, axis: str = DECLINE_AXIS
) -> None:
    """Revert a single capacity axis to its prior value — no first-observation
    regression on that axis (targeted analogue of ``floor_capacity_at_prior``)."""
    prior_v = float(getattr(prior.capacity_x, axis))
    updated_v = float(getattr(updated.capacity_x, axis))
    if updated_v < prior_v:
        setattr(updated.capacity_x, axis, prior_v)


def _mean_fatigue(state: UnifiedStateVector) -> float:
    vals = [float(getattr(state.fatigue_f, k)) for k in FatigueState.KEYS]
    return (sum(vals) / max(1, len(vals))) / 100.0


def _measurement_error_from_definition(
    definition: BenchmarkDefinition,
) -> policy.MeasurementError | None:
    """Optional per-definition MDC/SEM, read from ``standardization_rules`` JSONB
    (additive, no migration). Absent → the policy fallback CV governs."""
    rules: dict[str, Any] = definition.standardization_rules or {}
    mdc = rules.get("mdc")
    sem = rules.get("sem")
    if mdc is None and sem is None:
        return None
    return policy.MeasurementError(
        mdc=float(mdc) if mdc is not None else None,
        sem=float(sem) if sem is not None else None,
    )


def _targets_axis(mappings: list[Any], axis: str = DECLINE_AXIS) -> bool:
    return any(
        getattr(m, "target_vector", None) == "capacity"
        and getattr(m, "target_key", None) == axis
        for m in mappings
    )


async def _prior_watermark(
    db: AsyncSession, user_id: int, code: str, exclude_observation_id: int
) -> float | None:
    """Best currently valid demonstrated e1RM for the code, EXCLUDING the current
    observation — the prior a decline is measured against."""
    res = await db.execute(
        select(func.max(BenchmarkObservation.raw_value))
        .join(
            BenchmarkDefinition,
            BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id,
        )
        .where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkDefinition.code == code,
            BenchmarkObservation.id != exclude_observation_id,
            BenchmarkObservation.validity_status.notin_(("quarantined", "invalid")),
        )
    )
    return res.scalar_one_or_none()


async def evaluate_first_observation(
    db: AsyncSession,
    user_id: int,
    *,
    current: UnifiedStateVector,
    observation: BenchmarkObservation,
    definition: BenchmarkDefinition,
    mappings: list[Any],
    observed_raw: float,
) -> DeclineOutcome | None:
    """Gate a bidirectional ``max_strength`` observation through the decline policy.

    Returns ``None`` when the observation is not an interception case (not targeting
    ``max_strength``, no established prior watermark, or an upward move) — the caller
    then applies the normal bidirectional update. Otherwise the axis must be held at
    its prior (no first-observation regression); a *material* drop additionally opens
    a decline candidate. Persistence is deferred to the caller's commit; a candidate
    row is added best-effort (a shadow-capture failure never breaks the write).
    """
    if not _targets_axis(mappings):
        return None
    prior = await _prior_watermark(db, user_id, definition.code, observation.id)
    if prior is None or observed_raw >= prior:
        return None  # first measurement, or not a downward move → normal path

    error = _measurement_error_from_definition(definition)
    assessment = assess_decline(
        prior_mean=prior,
        observed_value=observed_raw,
        error=error,
        mean_fatigue=_mean_fatigue(current),
    )

    if not assessment.is_material:
        # Inside the measurement-error band: hold the axis (no regression), but no
        # durable transition and no candidate. It remains stored as history.
        return DeclineOutcome(
            hold_axis=True,
            applied_capacity_effect=APPLIED_NONE,
            decline_transition_status=TS_NO_MATERIAL_DECLINE,
            candidate=None,
        )

    severe = assessment.classification == policy.SEVERE_DECLINE
    candidate = _build_candidate(
        user_id=user_id,
        observation=observation,
        definition=definition,
        assessment=assessment,
        severe=severe,
    )
    try:
        db.add(candidate)
    except Exception:  # best-effort capture — never break the observation write
        logger.warning(
            "strength decline candidate capture failed for user %s (obs %s)",
            user_id, getattr(observation, "id", None), exc_info=True,
        )
        candidate = None

    status = TS_SAFETY_ROUTED if severe else TS_DECLINE_CANDIDATE
    return DeclineOutcome(
        hold_axis=True,
        applied_capacity_effect=APPLIED_NONE,
        decline_transition_status=status,
        candidate=candidate,
    )


def _build_candidate(
    *,
    user_id: int,
    observation: BenchmarkObservation,
    definition: BenchmarkDefinition,
    assessment: DeclineAssessment,
    severe: bool,
) -> StrengthDeclineCandidate:
    created = observation.observed_at or datetime.utcnow()
    occurrence = f"{user_id}:{definition.code}:{created.date().isoformat()}"
    return StrengthDeclineCandidate(
        user_id=user_id,
        capacity_axis=DECLINE_AXIS,
        benchmark_definition_id=definition.id,
        benchmark_code=definition.code,
        trigger_observation_id=observation.id,
        trigger_assessment_occurrence_id=occurrence,
        prior_mean=assessment.prior_mean,
        prior_variance=assessment.prior_variance,
        observed_value=assessment.observed_value,
        observation_variance=assessment.observation_variance,
        measurement_error_threshold=assessment.threshold.threshold,
        normalized_residual=assessment.delta_down,
        threshold_source=assessment.threshold.measurement_error_source,
        fatigue_readiness_context={"mean_fatigue": assessment.mean_fatigue},
        status=STATUS_SAFETY_ROUTED if severe else STATUS_ACTIVE,
        created_at=datetime.utcnow(),
        confirmation_deadline=datetime.utcnow() + timedelta(days=CONFIRMATION_WINDOW_DAYS),
        authority_policy_version=observation.authority_policy_version or oa.POLICY_VERSION,
        decline_policy_version=policy.POLICY_VERSION,
        resolution_reason=(
            "severe_unexplained_drop_routed_to_safety" if severe else None
        ),
    )
