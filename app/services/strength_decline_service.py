"""Downward strength-decline state machine — service layer (INT-02, ADR-0066).

Sits on the benchmark ingestion path. A protocol-valid ``bidirectional_update``
observation whose ``max_strength`` residual is *materially* downward does NOT rewrite
canonical capacity on first evidence: the axis is held at its prior and a decline
candidate is opened. Durable regression happens only after **independent
corroboration** — a second qualifying observation from a different assessment
occurrence, separated by the definition's minimum retest interval — and is applied as
a **bounded** estimator move, never an overwrite. A re-demonstration at/above the
watermark dismisses the candidate; the confirmation window expiring retires it.

State machine: ``active → confirmed | dismissed | expired | safety_routed``.

Design split: :func:`assess_decline` is **pure** (unit-tested directly) and owns the
provisional v1 variance model over the calibrated
:mod:`app.logic.strength_decline_policy` threshold math; the ``async`` functions are a
thin persistence + orchestration layer.

Provisional v1 (``strength_decline_policy_v1``, ``synthetic_and_expert_prior`` — NOT
calibrated; shadow before retuning): the materiality decision is in raw e1RM units
against the best currently valid demonstrated watermark; a fatigued observation is
treated as noisier (so a fatigued low test needs a larger drop to count); the
confirmed bounded move is applied in capacity-axis space and can only lower the axis.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic import observation_authority as oa
from app.logic import strength_decline_policy as policy
from app.logic.state_update_v0 import normalize_score01
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.strength_decline_candidate import (
    STATUS_ACTIVE,
    STATUS_CONFIRMED,
    STATUS_DISMISSED,
    STATUS_EXPIRED,
    STATUS_SAFETY_ROUTED,
    StrengthDeclineCandidate,
)
from app.schemas.engine_vectors import FatigueState
from app.schemas.state import UnifiedStateVector

logger = logging.getLogger(__name__)

DECLINE_AXIS = "max_strength"
# Capacity-axis ceiling for max_strength (0-100 scale; aerobic is the only 650 axis).
AXIS_CEILING = 100.0
# Provisional (not calibrated): mean fatigue of 1.0 doubles observation noise.
FATIGUE_NOISE_SCALE = 1.0
# Provisional window in which independent corroboration must arrive.
CONFIRMATION_WINDOW_DAYS = 90
# Provisional minimum separation between trigger and confirming observation when the
# definition states none. Null must NOT mean same-day confirmation is allowed.
FALLBACK_RETEST_INTERVAL_DAYS = 7
# Provisional bounded-update gain applied to the axis on corroborated confirmation.
CONFIRMED_GAIN = 0.5

# Applied-effect / transition-status stamps recorded on the observation.
APPLIED_NONE = oa.CE_NONE
APPLIED_BIDIRECTIONAL = oa.CE_BIDIRECTIONAL_UPDATE
TS_NO_MATERIAL_DECLINE = "no_material_decline"
TS_DECLINE_CANDIDATE = "decline_candidate"
TS_DECLINE_PENDING = "decline_pending"
TS_CONFIRMED_DECLINE = "confirmed_decline"
TS_CANDIDATE_DISMISSED = "candidate_dismissed"
TS_SAFETY_ROUTED = "safety_routed"

# Prescription-basis flag states (fork C staged rollout).
BASIS_MODE_OFF = "off"
BASIS_MODE_SHADOW = "shadow"
BASIS_MODE_ON = "on"


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


def confirmed_axis_posterior(
    *, prior_axis: float, observed_axis: float, gain: float = CONFIRMED_GAIN
) -> float:
    """Bounded axis-space posterior for a confirmed decline.

    ``prior + K·(observed − prior)`` clamped so a *confirmed decline* can only lower
    the axis (never raise it, even in the rare lagging-estimate case) and stays within
    ``[0, prior]``.
    """
    post = policy.bounded_posterior(prior_axis, observed_axis, gain)
    return max(0.0, min(prior_axis, post))


# --------------------------------------------------------------------------- #
# Outcome + pure helpers
# --------------------------------------------------------------------------- #

@dataclass
class BidirectionalOutcome:
    """What the ingestion caller should do with a bidirectional observation.

    ``intercepted=False`` → apply the normal bidirectional update. Otherwise: set
    ``apply_posterior`` on the axis if not None (a confirmed bounded decline), else
    ``hold_axis`` at prior if True (no first-observation regression).
    """

    intercepted: bool
    hold_axis: bool = False
    apply_posterior: float | None = None
    applied_capacity_effect: str = APPLIED_BIDIRECTIONAL
    decline_transition_status: str | None = None


_PASSTHROUGH = BidirectionalOutcome(intercepted=False)


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


def _occurrence(user_id: int, code: str, observed_at: datetime | None) -> str:
    day = observed_at.date().isoformat() if observed_at else "unknown"
    return f"{user_id}:{code}:{day}"


# --------------------------------------------------------------------------- #
# DB layer
# --------------------------------------------------------------------------- #

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


async def _active_candidate(
    db: AsyncSession, user_id: int, axis: str = DECLINE_AXIS
) -> StrengthDeclineCandidate | None:
    res = await db.execute(
        select(StrengthDeclineCandidate)
        .where(
            StrengthDeclineCandidate.user_id == user_id,
            StrengthDeclineCandidate.capacity_axis == axis,
            StrengthDeclineCandidate.status == STATUS_ACTIVE,
        )
        .order_by(StrengthDeclineCandidate.created_at.desc())
        .limit(1)
    )
    return res.scalars().first()


def _qualifies_as_confirmation(
    candidate: StrengthDeclineCandidate,
    *,
    observation: BenchmarkObservation,
    observed_raw: float,
    definition: BenchmarkDefinition,
    occurrence: str,
    assessment: DeclineAssessment,
) -> bool:
    """All independent-corroboration conditions must hold (never confirm from replay)."""
    if observation.id == candidate.trigger_observation_id:
        return False  # a candidate can never be confirmed by its own trigger
    if occurrence == candidate.trigger_assessment_occurrence_id:
        return False  # same assessment occasion
    if observed_raw >= candidate.prior_mean:
        return False  # not directionally consistent (a re-demonstration)
    if not assessment.is_material:
        return False  # inside the measurement-error band
    interval = definition.minimum_retest_interval_days or FALLBACK_RETEST_INTERVAL_DAYS
    if (observation.observed_at - candidate.created_at).days < interval:
        return False  # too soon — insufficient recovery separation
    return True


async def resolve_bidirectional_observation(
    db: AsyncSession,
    user_id: int,
    *,
    current: UnifiedStateVector,
    observation: BenchmarkObservation,
    definition: BenchmarkDefinition,
    mappings: list[Any],
    observed_raw: float,
) -> BidirectionalOutcome:
    """Route a bidirectional ``max_strength`` observation through the decline machine.

    Passthrough (normal bidirectional apply) when not targeting ``max_strength`` or no
    prior watermark exists. A re-demonstration at/above the watermark dismisses any
    active candidate. A downward observation confirms an active candidate when the
    corroboration conditions hold (→ bounded axis decline), else holds the axis; with
    no active candidate a material drop opens one.
    """
    if not _targets_axis(mappings):
        return _PASSTHROUGH
    prior = await _prior_watermark(db, user_id, definition.code, observation.id)
    if prior is None:
        return _PASSTHROUGH  # first measurement — nothing to decline from

    if observed_raw >= prior:
        # Re-demonstration at/above the watermark: dismiss an active candidate and
        # apply the (upward) observation normally.
        active = await _active_candidate(db, user_id)
        if active is not None:
            active.status = STATUS_DISMISSED
            active.resolved_at = datetime.now(UTC).replace(tzinfo=None)
            active.confirmation_observation_id = observation.id
            active.resolution_reason = "re_demonstrated_at_or_above_watermark"
            return BidirectionalOutcome(
                intercepted=True,
                applied_capacity_effect=APPLIED_BIDIRECTIONAL,
                decline_transition_status=TS_CANDIDATE_DISMISSED,
            )
        return _PASSTHROUGH

    # Downward vs the watermark.
    error = _measurement_error_from_definition(definition)
    mean_fatigue = _mean_fatigue(current)
    active = await _active_candidate(db, user_id)

    if active is not None:
        # Expire a stale candidate, then treat this observation as a fresh first one.
        if (
            active.confirmation_deadline is not None
            and observation.observed_at > active.confirmation_deadline
        ):
            active.status = STATUS_EXPIRED
            active.resolved_at = datetime.now(UTC).replace(tzinfo=None)
            active.resolution_reason = "confirmation_window_expired"
            active = None
        else:
            occurrence = _occurrence(user_id, definition.code, observation.observed_at)
            assessment = assess_decline(
                prior_mean=active.prior_mean, observed_value=observed_raw,
                error=error, mean_fatigue=mean_fatigue,
            )
            if _qualifies_as_confirmation(
                active, observation=observation, observed_raw=observed_raw,
                definition=definition, occurrence=occurrence, assessment=assessment,
            ):
                prior_axis = float(getattr(current.capacity_x, DECLINE_AXIS))
                observed_axis = _observed_axis(definition, observed_raw)
                posterior = (
                    confirmed_axis_posterior(prior_axis=prior_axis, observed_axis=observed_axis)
                    if observed_axis is not None
                    else prior_axis
                )
                active.status = STATUS_CONFIRMED
                active.resolved_at = datetime.now(UTC).replace(tzinfo=None)
                active.confirmation_observation_id = observation.id
                active.applied_posterior_mean = posterior
                active.resolution_reason = "confirmed_downward_evidence"
                return BidirectionalOutcome(
                    intercepted=True,
                    apply_posterior=posterior,
                    applied_capacity_effect=APPLIED_BIDIRECTIONAL,
                    decline_transition_status=TS_CONFIRMED_DECLINE,
                )
            # Downward but not (yet) a valid confirmation → hold, no new candidate.
            return BidirectionalOutcome(
                intercepted=True, hold_axis=True,
                applied_capacity_effect=APPLIED_NONE,
                decline_transition_status=TS_DECLINE_PENDING,
            )

    # No active candidate → first-observation assessment.
    return _first_observation_outcome(
        user_id=user_id, observation=observation, definition=definition,
        prior=prior, observed_raw=observed_raw, error=error, mean_fatigue=mean_fatigue,
        db=db,
    )


def _observed_axis(definition: BenchmarkDefinition, observed_raw: float) -> float | None:
    score01 = normalize_score01(
        definition.better_direction, observed_raw, definition.standardization_rules
    )
    if score01 is None:
        return None
    return score01 * AXIS_CEILING


def _first_observation_outcome(
    *,
    db: AsyncSession,
    user_id: int,
    observation: BenchmarkObservation,
    definition: BenchmarkDefinition,
    prior: float,
    observed_raw: float,
    error: policy.MeasurementError | None,
    mean_fatigue: float,
) -> BidirectionalOutcome:
    assessment = assess_decline(
        prior_mean=prior, observed_value=observed_raw,
        error=error, mean_fatigue=mean_fatigue,
    )
    if not assessment.is_material:
        # Inside the error band: hold (no regression), no candidate.
        return BidirectionalOutcome(
            intercepted=True, hold_axis=True,
            applied_capacity_effect=APPLIED_NONE,
            decline_transition_status=TS_NO_MATERIAL_DECLINE,
        )
    severe = assessment.classification == policy.SEVERE_DECLINE
    candidate = _build_candidate(
        user_id=user_id, observation=observation, definition=definition,
        assessment=assessment, severe=severe,
    )
    try:
        db.add(candidate)
    except Exception:  # best-effort capture — never break the observation write
        logger.warning(
            "strength decline candidate capture failed for user %s (obs %s)",
            user_id, getattr(observation, "id", None), exc_info=True,
        )
    if severe:
        _route_severe_to_safety(user_id, definition, assessment)
    return BidirectionalOutcome(
        intercepted=True, hold_axis=True,
        applied_capacity_effect=APPLIED_NONE,
        decline_transition_status=TS_SAFETY_ROUTED if severe else TS_DECLINE_CANDIDATE,
    )


@dataclass(frozen=True)
class DeclineObservability:
    """Observability rollup for the decline state machine (INT-02, ADR-0066)."""

    candidates_total: int
    active: int
    confirmed: int
    dismissed: int
    expired: int
    safety_routed: int
    temporary_prescription_caps: int  # active candidates each impose a ceiling
    confirmed_decline_magnitude: float  # mean raw residual of confirmed declines
    durable_strength_regressions_from_one_observation: int  # THE invariant — must be 0


async def decline_observability(
    db: AsyncSession, user_id: int | None = None
) -> DeclineObservability:
    """Aggregate the decline ledger for monitoring.

    The critical invariant ``durable_strength_regressions_from_one_observation`` counts
    confirmed declines that lack a *distinct* corroborating observation — durable
    regression from a single observation. It is 0 by construction (confirmation
    requires a distinct observation from a different occasion) and is asserted in tests.
    """

    def _scope(stmt: Any) -> Any:
        return stmt if user_id is None else stmt.where(
            StrengthDeclineCandidate.user_id == user_id
        )

    rows = (await db.execute(
        _scope(
            select(StrengthDeclineCandidate.status, func.count()).group_by(
                StrengthDeclineCandidate.status
            )
        )
    )).all()
    counts = {status: int(n) for status, n in rows}

    magnitude = (await db.execute(
        _scope(
            select(func.avg(StrengthDeclineCandidate.normalized_residual)).where(
                StrengthDeclineCandidate.status == STATUS_CONFIRMED
            )
        )
    )).scalar_one_or_none()

    durable_one_obs = int((await db.execute(
        _scope(
            select(func.count()).select_from(StrengthDeclineCandidate).where(
                StrengthDeclineCandidate.status == STATUS_CONFIRMED,
                or_(
                    StrengthDeclineCandidate.confirmation_observation_id.is_(None),
                    StrengthDeclineCandidate.confirmation_observation_id
                    == StrengthDeclineCandidate.trigger_observation_id,
                ),
            )
        )
    )).scalar_one())

    return DeclineObservability(
        candidates_total=sum(counts.values()),
        active=counts.get(STATUS_ACTIVE, 0),
        confirmed=counts.get(STATUS_CONFIRMED, 0),
        dismissed=counts.get(STATUS_DISMISSED, 0),
        expired=counts.get(STATUS_EXPIRED, 0),
        safety_routed=counts.get(STATUS_SAFETY_ROUTED, 0),
        temporary_prescription_caps=counts.get(STATUS_ACTIVE, 0),
        confirmed_decline_magnitude=float(magnitude) if magnitude is not None else 0.0,
        durable_strength_regressions_from_one_observation=durable_one_obs,
    )


def _route_severe_to_safety(
    user_id: int, definition: BenchmarkDefinition, assessment: DeclineAssessment
) -> None:
    """Route a severe unexplained drop to the existing safety/review surface.

    Ownership boundary (ADR-0066): the decline policy only *identifies* and *routes* a
    severe drop and records the result (``status = safety_routed``, this signal). The
    existing safety subsystem owns the response; NO new clinical/contraindication logic
    is introduced here. Canonical state is unchanged and prescription is conservatively
    constrained — those outcomes are compatible with a safety review.
    """
    logger.warning(
        "SAFETY-ROUTE severe unexplained strength drop: user=%s code=%s prior=%.1f "
        "observed=%.1f delta=%.1f threshold=%.2f — canonical state UNCHANGED, "
        "candidate=safety_routed (not auto-detrained)",
        user_id, definition.code, assessment.prior_mean, assessment.observed_value,
        assessment.delta_down, assessment.threshold.threshold,
    )


# --------------------------------------------------------------------------- #
# Candidate-aware prescription basis (T7, fork C staged rollout)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BasisDecision:
    legacy_basis: float
    normal_basis: float
    candidate_aware_basis: float
    selected_basis: float
    ceiling: float | None
    candidate_id: int | None
    mode: str


def axis_to_raw(axis_value: float, rules: dict[str, Any] | None) -> float | None:
    """Project a normalized capacity-axis value back to raw e1RM for a definition's
    scale: ``floor + (axis/ceiling)·(cap − floor)``. Reflects confirmed declines (the
    axis drops); unlike the latest raw observation it is not one-test-reactive."""
    if not rules:
        return None
    floor = rules.get("floor")
    cap = rules.get("cap")
    if floor is None or cap is None:
        return None
    return float(floor) + (axis_value / AXIS_CEILING) * (float(cap) - float(floor))


def _measurement_error_from_rules(
    rules: dict[str, Any] | None,
) -> policy.MeasurementError | None:
    if not rules:
        return None
    mdc = rules.get("mdc")
    sem = rules.get("sem")
    if mdc is None and sem is None:
        return None
    return policy.MeasurementError(
        mdc=float(mdc) if mdc is not None else None,
        sem=float(sem) if sem is not None else None,
    )


def select_basis(*, mode: str, legacy: float, candidate_aware: float) -> float:
    """The flag governs the whole selection: ``on`` uses the candidate-aware basis
    (latest-raw is no longer authority); otherwise legacy latest-raw is selected."""
    return candidate_aware if mode == BASIS_MODE_ON else legacy


async def resolve_prescription_basis(
    db: AsyncSession,
    user_id: int,
    *,
    code: str,
    latest_raw: float,
    current_axis: float | None,
    rules: dict[str, Any] | None,
    mode: str,
) -> BasisDecision:
    """Compute the legacy and candidate-aware e1RM bases for a lift and select per mode.

    ``normal_basis`` = canonical current capacity projected to raw e1RM (reflects
    confirmed declines); an active decline candidate for this same lift caps it at a
    conservative ceiling (``observed + measurement_error``). In ``on`` mode the
    selected basis is ``min(normal, ceiling)`` and the latest raw observation is no
    longer authority; ``shadow`` records both but still selects legacy.
    """
    legacy = float(latest_raw)
    normal = axis_to_raw(current_axis, rules) if current_axis is not None else None
    if normal is None:
        normal = legacy
    active = await _active_candidate(db, user_id)
    ceiling: float | None = None
    candidate_id: int | None = None
    if active is not None and active.benchmark_code == code:
        me = policy.resolve_measurement_error(
            _measurement_error_from_rules(rules), active.observed_value
        ).value
        ceiling = policy.temporary_ceiling(active.observed_value, me)
        candidate_id = active.id
    candidate_aware = min(normal, ceiling) if ceiling is not None else normal
    selected = select_basis(mode=mode, legacy=legacy, candidate_aware=candidate_aware)
    logger.info(
        "decline prescription basis user=%s code=%s mode=%s legacy=%.2f normal=%.2f "
        "candidate_aware=%.2f selected=%.2f ceiling=%s candidate=%s",
        user_id, code, mode, legacy, normal, candidate_aware, selected, ceiling, candidate_id,
    )
    return BasisDecision(
        legacy_basis=legacy, normal_basis=normal, candidate_aware_basis=candidate_aware,
        selected_basis=selected, ceiling=ceiling, candidate_id=candidate_id, mode=mode,
    )


def _build_candidate(
    *,
    user_id: int,
    observation: BenchmarkObservation,
    definition: BenchmarkDefinition,
    assessment: DeclineAssessment,
    severe: bool,
) -> StrengthDeclineCandidate:
    trigger_time = observation.observed_at
    occurrence = _occurrence(user_id, definition.code, trigger_time)
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
        created_at=trigger_time,
        confirmation_deadline=trigger_time + timedelta(days=CONFIRMATION_WINDOW_DAYS),
        authority_policy_version=observation.authority_policy_version or oa.POLICY_VERSION,
        decline_policy_version=policy.POLICY_VERSION,
        resolution_reason=(
            "severe_unexplained_drop_routed_to_safety" if severe else None
        ),
    )
