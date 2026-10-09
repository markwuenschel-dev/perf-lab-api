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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import AsyncSessionLocal
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
from app.models.strength_decline_shadow import StrengthDeclineShadow
from app.repositories.benchmark_observation_repository import decline_prior_watermark
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
    """Best currently valid DEMONSTRATED e1RM for the code, EXCLUDING the current
    observation — the prior a decline is measured against. Measured max tests only: a training
    estimate (however high) is not strength the athlete demonstrated, so it cannot make a
    later honest test look like a decline."""
    return await decline_prior_watermark(
        db, user_id, code, exclude_observation_id=exclude_observation_id
    )


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


# A candidate is only as good as the evidence it was opened on. It is SUPPORTED while all of these
# still hold, each recomputed from the rows as they are now:
#   * it was opened under the current decline policy;
#   * its trigger is still a valid, unquarantined measured test that ingestion would still let act
#     on strength: the EFFECTIVE authority (stored effect met with the effect derived from the
#     row's current provenance, exactly as ingestion computes it) is bidirectional. A stored
#     effect alone is not enough: provenance corrected since (semantics now `estimated`) changes
#     what the row is entitled to, and the database constraint does not enforce the whole rule;
#   * its trigger still has the value AND the time the candidate recorded (the retest interval is
#     measured from `created_at`, so a trigger moved in time would leave a stale clock running);
#   * its prior is exactly the prior derivable for that moment: decline-protection evidence strictly
#     before the trigger in `(observed_at, id)` order (so a test tied with the trigger that arrived
#     earlier is in it, one that arrived later is not). A prior that was a training estimate under the old
#     watermark no longer matches; neither does one a backdated higher test (dated before the
#     trigger) or a quarantine has changed; and a higher maximum today never rehabilitates a
#     candidate opened on the wrong evidence;
#   * nothing demonstrated AFTER the trigger (strictly after it in that same order, so a tied test
#     that arrived later counts) has re-demonstrated at or above its prior. A test the
#     machine never saw (it arrived record-only) must not leave the candidate's ceiling standing.
#     The observation being processed is left out of this: the machine handles its own
#     re-demonstration, with its own reason.
# An unsupported candidate must not confirm a regression or cap a prescription.
_PRIOR_EPSILON = 1e-6
RESOLUTION_PRIOR_NOT_DEMONSTRATED = "prior_not_demonstrated"
RESOLUTION_TRIGGER_NOT_CURRENT = "trigger_not_a_current_measurement"
RESOLUTION_REDEMONSTRATED_LATER = "re_demonstrated_by_a_later_test"


async def _unsupported_reason(
    db: AsyncSession, candidate: StrengthDeclineCandidate, *, current_observation_id: int | None = None
) -> str | None:
    """Why this candidate can no longer stand, or None while it is supported."""
    if candidate.decline_policy_version != policy.POLICY_VERSION:
        return RESOLUTION_PRIOR_NOT_DEMONSTRATED
    trigger = (await db.execute(
        select(BenchmarkObservation)
        .where(BenchmarkObservation.id == candidate.trigger_observation_id)
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if (
        trigger is None
        or trigger.validity_status != "valid"
        or trigger.quarantined_at is not None
        or oa.meet(trigger.capacity_effect or oa.CE_NONE, oa.capacity_effect_of(trigger))
        != oa.CE_BIDIRECTIONAL_UPDATE
        or trigger.raw_value != candidate.observed_value
        or trigger.observed_at != candidate.created_at
    ):
        return RESOLUTION_TRIGGER_NOT_CURRENT
    where_it_was = (trigger.observed_at, trigger.id)
    prior = await decline_prior_watermark(
        db, candidate.user_id, candidate.benchmark_code, before=where_it_was,
    )
    if prior is None or abs(candidate.prior_mean - prior) > _PRIOR_EPSILON:
        return RESOLUTION_PRIOR_NOT_DEMONSTRATED
    since = await decline_prior_watermark(
        db, candidate.user_id, candidate.benchmark_code,
        exclude_observation_id=current_observation_id, after=where_it_was,
    )
    if since is not None and since >= candidate.prior_mean - _PRIOR_EPSILON:
        return RESOLUTION_REDEMONSTRATED_LATER
    return None


async def _candidate_is_supported(
    db: AsyncSession, candidate: StrengthDeclineCandidate, *, current_observation_id: int | None = None
) -> bool:
    return await _unsupported_reason(db, candidate, current_observation_id=current_observation_id) is None


async def _current_active_candidate(
    db: AsyncSession, user_id: int, axis: str = DECLINE_AXIS, *,
    current_observation_id: int | None = None,
) -> StrengthDeclineCandidate | None:
    """The active candidate, after retiring one that its own evidence no longer supports."""
    active = await _active_candidate(db, user_id, axis)
    if active is None:
        return None
    reason = await _unsupported_reason(db, active, current_observation_id=current_observation_id)
    if reason is None:
        return active
    active.status = STATUS_DISMISSED
    active.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    active.resolution_reason = reason
    return None


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
    # Retire a candidate opened against a prior that is no longer demonstrated BEFORE anything
    # reads it, including when there is no demonstrated prior left at all.
    await _current_active_candidate(db, user_id, current_observation_id=observation.id)
    prior = await _prior_watermark(db, user_id, definition.code, observation.id)
    if prior is None:
        return _PASSTHROUGH  # first measurement — nothing to decline from

    if observed_raw >= prior:
        # Re-demonstration at/above the watermark: dismiss an active candidate and
        # apply the (upward) observation normally.
        active = await _current_active_candidate(db, user_id, current_observation_id=observation.id)
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
    active = await _current_active_candidate(db, user_id, current_observation_id=observation.id)

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

# The formula behind the temporary prescription ceiling (``policy.temporary_ceiling``):
# the observed low value plus the resolved measurement error. Recorded on every shadow
# row so a later reader can tell which ceiling definition produced it.
CEILING_SEMANTICS = "observed_value_plus_measurement_error"


@dataclass(frozen=True)
class StrengthDeclineShadowPayload:
    """An immutable, fully-resolved shadow observation — primitives and ids only.

    Deliberately carries no ORM instance and no session reference. The basis
    resolution runs *inside* the prescription's transaction, but the shadow row must
    be written *after* that transaction commits (see
    ``persist_strength_decline_shadow_best_effort``). Handing an attached ORM object
    across that boundary is what couples telemetry to the request's session state;
    a frozen value object cannot.
    """

    candidate_id: int
    user_id: int
    trigger_observation_id: int
    capacity_axis: str
    benchmark_code: str
    mode: str
    candidate_outcome: str
    prior_mean: float
    prior_variance: float
    observed_value: float
    observation_variance: float
    threshold_source: str
    threshold_value: float
    legacy_basis: float
    normal_basis: float
    candidate_aware_basis: float
    selected_basis: float
    ceiling: float
    absolute_delta: float
    relative_delta: float | None
    ceiling_semantics: str
    decline_policy_version: str
    authority_policy_version: str


@dataclass(frozen=True)
class BasisDecision:
    legacy_basis: float
    normal_basis: float
    candidate_aware_basis: float
    selected_basis: float
    ceiling: float | None
    candidate_id: int | None
    mode: str
    # Present only when an active candidate governed this code — otherwise there is no
    # counterfactual to record. The caller persists it AFTER the production commit;
    # resolving a basis never writes it.
    shadow_payload: StrengthDeclineShadowPayload | None = None


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
    # Read-only: a stale candidate is ignored here and retired by the next observation write (this
    # resolver must stay side-effect free; see test_resolver_purity).
    if active is not None and not await _candidate_is_supported(db, active):
        active = None
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
    payload: StrengthDeclineShadowPayload | None = None
    if active is not None and ceiling is not None:
        payload = _build_shadow_payload(
            active=active, mode=mode, legacy=legacy, normal=normal,
            candidate_aware=candidate_aware, selected=selected, ceiling=ceiling,
        )
    return BasisDecision(
        legacy_basis=legacy, normal_basis=normal, candidate_aware_basis=candidate_aware,
        selected_basis=selected, ceiling=ceiling, candidate_id=candidate_id, mode=mode,
        shadow_payload=payload,
    )


def _build_shadow_payload(
    *,
    active: StrengthDeclineCandidate,
    mode: str,
    legacy: float,
    normal: float,
    candidate_aware: float,
    selected: float,
    ceiling: float,
) -> StrengthDeclineShadowPayload:
    """Pure projection of an evaluated candidate into an immutable shadow row.

    No I/O of any kind — every attribute is read off the already-loaded candidate.
    ``resolve_prescription_basis`` must stay side-effect free with respect to shadow
    telemetry; see ``test_resolver_purity``.
    """
    absolute_delta = candidate_aware - legacy
    return StrengthDeclineShadowPayload(
        candidate_id=active.id,
        user_id=active.user_id,
        trigger_observation_id=active.trigger_observation_id,
        capacity_axis=active.capacity_axis,
        benchmark_code=active.benchmark_code,
        mode=mode,
        candidate_outcome=active.status,
        prior_mean=active.prior_mean,
        prior_variance=active.prior_variance,
        observed_value=active.observed_value,
        observation_variance=active.observation_variance,
        threshold_source=active.threshold_source,
        threshold_value=active.measurement_error_threshold,
        legacy_basis=legacy,
        normal_basis=normal,
        candidate_aware_basis=candidate_aware,
        selected_basis=selected,
        ceiling=ceiling,
        absolute_delta=absolute_delta,
        relative_delta=(absolute_delta / legacy) if legacy else None,
        ceiling_semantics=CEILING_SEMANTICS,
        decline_policy_version=active.decline_policy_version,
        authority_policy_version=active.authority_policy_version,
    )


async def persist_strength_decline_shadow_best_effort(
    payload: StrengthDeclineShadowPayload | None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Write one shadow row AFTER the production prescription has committed.

    Three properties this function exists to guarantee, each of which the first
    attempt at this writer got wrong:

    1. **Its own transaction.** A fresh session from ``session_factory``, never the
       request's. A failed telemetry transaction cannot poison the request session,
       cannot roll back the committed prescription, and rolls back cleanly by itself.
    2. **Real isolation.** The guard wraps the actual I/O (execute + commit), not
       ``db.add()`` — ``db.add()`` stages in memory and does no I/O, so a guard around
       it catches nothing and the true failure surfaces later, at commit, uncaught.
    3. **Atomic idempotency.** ``INSERT ... ON CONFLICT DO NOTHING`` lets the unique
       constraint arbitrate. A SELECT-before-INSERT check is a TOCTOU race that
       manufactures the very unique-violation it means to avoid: two concurrent
       prescriptions both see no row, both insert, one 500s.
    """
    if payload is None:
        return
    # Resolved at call time, not as a default argument: a default binds AsyncSessionLocal
    # at import and cannot be monkeypatched, which would silently send test writes to the
    # app's configured DATABASE_URL instead of the test database — a test that passes
    # while proving nothing.
    factory = session_factory or AsyncSessionLocal
    try:
        async with factory() as telemetry_db:
            async with telemetry_db.begin():
                stmt = pg_insert(StrengthDeclineShadow).values(
                    user_id=payload.user_id,
                    trigger_observation_id=payload.trigger_observation_id,
                    candidate_id=payload.candidate_id,
                    capacity_axis=payload.capacity_axis,
                    benchmark_code=payload.benchmark_code,
                    mode=payload.mode,
                    candidate_outcome=payload.candidate_outcome,
                    prior_mean=payload.prior_mean,
                    prior_variance=payload.prior_variance,
                    observed_value=payload.observed_value,
                    observation_variance=payload.observation_variance,
                    threshold_source=payload.threshold_source,
                    threshold_value=payload.threshold_value,
                    legacy_basis=payload.legacy_basis,
                    normal_basis=payload.normal_basis,
                    candidate_aware_basis=payload.candidate_aware_basis,
                    selected_basis=payload.selected_basis,
                    ceiling=payload.ceiling,
                    absolute_delta=payload.absolute_delta,
                    relative_delta=payload.relative_delta,
                    ceiling_semantics=payload.ceiling_semantics,
                    decline_policy_version=payload.decline_policy_version,
                    authority_policy_version=payload.authority_policy_version,
                    computed_at=datetime.now(UTC).replace(tzinfo=None),
                    decision_impact="none_shadow_only",
                ).on_conflict_do_nothing(
                    constraint="uq_strength_decline_shadow_trigger_axis_policy"
                )
                await telemetry_db.execute(stmt)
    except Exception:
        logger.exception(
            "strength_decline_shadow_write_failed candidate=%s observation=%s",
            payload.candidate_id, payload.trigger_observation_id,
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
