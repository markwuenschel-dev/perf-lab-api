"""
Current production athlete state evolution engine.

Handles:
- Multi-timescale fatigue decay
- Tissue load accumulation
- Capacity adaptation driven by AdaptationContribution vectors
- Cross-talk between domains
- Benchmark observation assimilation with correct timestamps

This is the preferred module for state updates. See also:
- `app.logic.cross_talk`
- `app.services.state_service`
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.vectors import CapacityConfidence, FatigueState, TissueState, capacity_ceiling
from app.engine.parameters import EngineParameters, default_parameters
from app.engine.phi_table import default_phi_for_row
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic import cross_talk
from app.logic.benchmark_validity import BenchmarkValidityProfile, effective_variance
from app.logic.interference import directional_interference_multiplier
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog

logger = logging.getLogger(__name__)

_VECTOR_ATTR = {"capacity": "capacity_x", "fatigue": "fatigue_f", "tissue": "tissue_t"}


def _read_axis(state: UnifiedStateVector, vector: str, key: str) -> float:
    sub = getattr(state, _VECTOR_ATTR[vector])
    return float(getattr(sub, key))


def _write_axis(state: UnifiedStateVector, vector: str, key: str, value: float) -> None:
    sub = getattr(state, _VECTOR_ATTR[vector])
    cap = capacity_ceiling(key) if vector == "capacity" else 100.0
    setattr(sub, key, max(0.0, min(cap, value)))


def floor_capacity_at_prior(
    prior: UnifiedStateVector, updated: UnifiedStateVector
) -> UnifiedStateVector:
    """Non-regressing ratchet for the ``upward_lower_bound`` capacity_effect (ADR-0058).

    Given the prior state and a candidate post-observation state, clamp every
    capacity axis up to at least its prior value so a lower-bound/estimated
    observation can only raise a floor, never pull capacity down. Fatigue and
    tissue vectors are untouched (they are transient, re-driven each session).
    """
    from app.domain.vectors import CapacityState

    for key in CapacityState.KEYS:
        prior_v = _read_axis(prior, "capacity", key)
        new_v = _read_axis(updated, "capacity", key)
        if new_v < prior_v:
            _write_axis(updated, "capacity", key, prior_v)
    return updated


def capacity_increased(
    prior: UnifiedStateVector, updated: UnifiedStateVector, *, eps: float = 1e-9
) -> bool:
    """True iff any capacity axis in ``updated`` exceeds ``prior``.

    Lets the ``upward_lower_bound`` / ``initialize_prior`` handlers skip persisting a
    no-op state row when a lower-bound observation lands *below* the current
    watermark (it raised nothing) — a training-derived e1RM below your measured
    max writes history only, never a redundant capacity row.
    """
    from app.domain.vectors import CapacityState

    return any(
        _read_axis(updated, "capacity", key) - _read_axis(prior, "capacity", key) > eps
        for key in CapacityState.KEYS
    )


def _mapping_delta(
    mapping: Any,
    signal: float,
    observation_weight: float,
) -> float:
    cfg: dict[str, Any] = mapping.config or {}
    scale = float(cfg.get("scale", 30.0))
    amp = float(cfg.get("amp", 2.5))
    coef = float(mapping.coefficient) * observation_weight
    intercept = float(mapping.intercept)
    mt = mapping.mapping_type

    if mt == "direct":
        r = signal - intercept
        return coef * amp * math.tanh(r / max(1e-6, scale))
    if mt == "inverse":
        r = intercept - signal
        return coef * amp * math.tanh(r / max(1e-6, scale))
    if mt == "logistic":
        k = float(cfg.get("k", 0.1))
        p = 1.0 / (1.0 + math.exp(-k * (signal - intercept)))
        return coef * amp * (p - 0.5) * 2.0
    if mt == "ratio_threshold":
        thr = float(cfg.get("threshold", 1.0))
        denom = float(cfg.get("denom", 1.0))
        ratio = signal / max(1e-6, denom)
        return coef * amp if ratio > thr else 0.0
    if mt == "bounded":
        r = signal - intercept
        return coef * amp * math.tanh(r / max(1e-6, scale))
    return 0.0


def normalize_score01(
    better_direction: str,
    raw_value: float,
    rules: dict[str, Any] | None,
) -> float | None:
    """Backend normalization: raw measurement → performance score in [0, 1] (higher = better).

    Uses per-definition anchors in ``standardization_rules`` —
    ``{"floor": <worst raw>, "cap": <best raw>}`` — and is direction-aware. Returns
    None when anchors are missing so the caller can fall back. The backend, not the
    client, owns this conversion (ADR-0034).
    """
    if not rules:
        return None
    floor = rules.get("floor")
    cap = rules.get("cap")
    if floor is None or cap is None or float(floor) == float(cap):
        return None
    if better_direction == "lower":
        frac = (float(floor) - float(raw_value)) / (float(floor) - float(cap))
    else:
        frac = (float(raw_value) - float(floor)) / (float(cap) - float(floor))
    return max(0.0, min(1.0, frac))


def _apply_capacity_residual(
    s: UnifiedStateVector,
    mapping: Any,
    score01: float,
    observation_weight: float,
    p: EngineParameters,
    validity_profile: BenchmarkValidityProfile | None = None,
) -> None:
    """Signed, confidence-scaled residual correction of a capacity axis (ADR-0034).

    The model's expectation for the axis is its current value (as a fraction of the
    axis ceiling); the measurement is ``score01``. We move the axis toward the
    measurement by a Kalman gain set by how unsure we are (low confidence → big move),
    then shrink that axis's variance. A below-expectation test pulls the axis *down*.

    When a validity_profile is supplied, uses profile-specific effective variance
    (R_eff) and per-axis mapping strength instead of the generic
    confidence_measured_variance. This reduces update strength for noisy/fatigue-
    sensitive benchmarks without suppressing valid 1RM-style tests. The None path
    is byte-for-byte identical to the pre-Task-2 behaviour.
    """
    key = mapping.target_key
    try:
        cur = _read_axis(s, "capacity", key)
    except AttributeError:
        # An unknown target_key silently dropped the whole capacity correction
        # (INT-26). Keep it non-fatal, but make the dropped assimilation visible
        # instead of a silent no-op.
        logger.warning(
            "capacity residual skipped: unknown target_key %r (mapping id=%s) — "
            "not a CapacityState axis; check benchmark mapping config",
            key,
            getattr(mapping, "id", "?"),
        )
        return
    ceiling = capacity_ceiling(key)
    expected01 = cur / ceiling if ceiling > 0 else 0.0
    residual01 = score01 - expected01
    prior_var = float(getattr(s.capacity_confidence, key, 1.0))

    if validity_profile is not None:
        r_eff = effective_variance(validity_profile, s)
        mapping_strength = validity_profile.mapping_strength.get(key, float(mapping.coefficient))
        # Modified Kalman gain: K = P * m / (m² * P + R_eff)
        # Effective update on state = m * K = P * m² / (m² * P + R_eff)
        gain = prior_var * mapping_strength / (mapping_strength ** 2 * prior_var + max(1e-9, r_eff))
        new_v = cur + mapping_strength * gain * residual01 * ceiling
        if mapping.min_value is not None:
            new_v = max(new_v, float(mapping.min_value))
        if mapping.max_value is not None:
            new_v = min(new_v, float(mapping.max_value))
        _write_axis(s, "capacity", key, new_v)
        conf_post = max(0.0, (1.0 - gain * mapping_strength) * prior_var)
        setattr(s.capacity_confidence, key, conf_post)
    else:
        weight = max(0.0, float(mapping.coefficient))  # mapping informativeness, ~[0, 1]
        meas_var = p.confidence_measured_variance / max(0.1, float(observation_weight))
        gain = kalman_gain(prior_var, meas_var)

        new_v = cur + weight * gain * residual01 * ceiling
        if mapping.min_value is not None:
            new_v = max(new_v, float(mapping.min_value))
        if mapping.max_value is not None:
            new_v = min(new_v, float(mapping.max_value))
        _write_axis(s, "capacity", key, new_v)
        # A measurement reduces uncertainty about the axis.
        setattr(s.capacity_confidence, key, max(0.0, (1.0 - gain) * prior_var))


def apply_benchmark_observation(
    prev_state: UnifiedStateVector,
    *,
    raw_value: float,
    normalized_value: float | None,
    better_direction: str,
    observation_weight: float,
    mappings: Sequence[Any],
    observed_at: datetime | None = None,
    score01: float | None = None,
    validity_profile: BenchmarkValidityProfile | None = None,
) -> UnifiedStateVector:
    """
    Assimilate a benchmark observation into state (no full EKF — ADR-0015).

    Capacity axes use a signed residual against the model's expectation, scaled by
    per-axis confidence (ADR-0034/0036): when the caller supplies ``score01`` (a
    backend-normalized [0, 1] score from the definition's standardization_rules), the
    correction is bidirectional and discriminative. Falling back, a 0-100
    ``normalized_value`` is treated as a score; lacking both, capacity (and all
    fatigue/tissue) maps use the legacy additive nudge so un-normalized definitions
    still move state.

    `observed_at` anchors the state timestamp to when the test was performed.
    """
    s = prev_state.model_copy(deep=True)
    p = default_parameters()

    if score01 is None and normalized_value is not None:
        score01 = max(0.0, min(1.0, float(normalized_value) / 100.0))

    # Legacy signal (with lower-is-better inversion) for non-residual maps.
    base = normalized_value if normalized_value is not None else raw_value
    if better_direction == "lower":
        signal = 1.0 / max(float(base), 1e-9)
    else:
        signal = float(base)

    for m in mappings:
        if m.target_vector not in _VECTOR_ATTR:
            continue
        if m.target_vector == "capacity" and score01 is not None:
            _apply_capacity_residual(s, m, score01, observation_weight, p, validity_profile)
            continue
        # Legacy additive nudge: fatigue/tissue, or capacity without normalization.
        try:
            cur = _read_axis(s, m.target_vector, m.target_key)
        except AttributeError:
            continue
        delta = _mapping_delta(m, signal, observation_weight)
        new_v = cur + delta
        if m.min_value is not None:
            new_v = max(new_v, float(m.min_value))
        if m.max_value is not None:
            new_v = min(new_v, float(m.max_value))
        _write_axis(s, m.target_vector, m.target_key, new_v)

    legacy = sync_legacy_from_vectors(s.capacity_x, s.fatigue_f, s.tissue_t)
    s.c_met_aerobic = legacy["c_met_aerobic"]
    s.c_nm_force = legacy["c_nm_force"]
    s.c_struct = legacy["c_struct"]
    s.b_met_anaerobic = legacy["b_met_anaerobic"]
    s.f_met_systemic = legacy["f_met_systemic"]
    s.f_nm_peripheral = legacy["f_nm_peripheral"]
    s.f_nm_central = legacy["f_nm_central"]
    s.f_struct_damage = legacy["f_struct_damage"]

    # Use observation timestamp for chronological correctness
    if observed_at is not None:
        s.timestamp = observed_at
    else:
        s.timestamp = datetime.now(UTC)

    return s


def _exp_decay(value: float, hours: float, tau: float) -> float:
    if value <= 0.01:
        return 0.0
    return value * math.exp(-hours / max(1e-6, tau))


# Population centre and spread of the 1-10 wellness self-report scale, used to z-score
# a report before it enters the clearance exponent. A report equal to the centre is
# neutral (z = 0), i.e. it neither speeds nor slows fatigue clearance.
RECOVERY_WELLNESS_CENTRE = 7.0
RECOVERY_WELLNESS_SD = 2.0


def _wellness_z(value: float | None, clip: float) -> float:
    """Clipped z-score of a 1-10 wellness self-report, or exactly 0.0 when unknown.

    Variables and units
    -------------------
    ``value``  x   : self-report, dimensionless ordinal on [1, 10], higher = better, or
                     ``None`` when no check-in exists for the session.
    ``mu``         : ``RECOVERY_WELLNESS_CENTRE`` = 7.0, same units as x.
    ``sigma``      : ``RECOVERY_WELLNESS_SD`` = 2.0 points, strictly positive.
    ``clip``       : ``params.recovery_zscore_scale`` = 2.0, in standard deviations.
    Output is a dimensionless z-score in [-clip, +clip].

    Equation
    --------
    Reported::  z(x) = clip_[-c, c]( (x - mu) / sigma )
    Unknown ::  z(None) = 0.0   by definition — the identity element of the exponent.

    ADR-0049 reconciliation. Before this change the caller substituted a bare ``7.0``
    for an unknown input, while ``WorkoutLog`` imputed ``5.0`` for the very same field —
    two different silent fills for one concept, and the workout path never reached the
    ``None`` branch at all because the schema had already fabricated 5.0. The single
    convention now is **unknown contributes nothing to the exponent**: z = 0, so that
    input's factor ``exp(beta * 0) = 1``, leaving any *other* measured input's effect
    intact. This is the identical convention the sibling shadow implementation already
    ships (``app/logic/recovery_telemetry.clearance_multiplier``, which ``continue``s
    past a ``None`` signal), and it is the same "unknown means no adjustment" rule the
    dose engine's human-factor gain uses.

    Writing z = 0 explicitly, rather than substituting the value that happens to
    z-score to zero, also decouples the missing-data rule from the calibration of
    ``mu``: if the centre is ever retuned away from 7.0, unknown stays neutral instead
    of silently becoming a penalty or a bonus.

    Boundary behaviour, monotonicity, stability
    -------------------------------------------
    - Monotone non-decreasing in x; strictly increasing on the unclipped interior.
    - x = mu = 7 gives z = 0, identical to the unknown branch's value but reached for a
      different reason: "the athlete reported a neutral day" vs "nobody reported".
      The two remain distinguishable upstream via the labelled dose provenance.
    - Over the full scale, (x - mu)/sigma spans [-3.0, +1.5]; the clip at c = 2.0 binds
      only the poor-report tail, at x <= 3.
    - sigma is a non-zero literal, so no division-by-zero guard is needed; the result is
      finite and bounded, which is what keeps the caller's ``math.exp`` far from
      overflow.
    """
    if value is None:
        return 0.0
    z = (value - RECOVERY_WELLNESS_CENTRE) / RECOVERY_WELLNESS_SD
    return max(-clip, min(clip, z))


def recovery_clearance_multiplier(
    axis: str,
    sleep_quality: float | None,
    life_stress_inverse: float | None,
    params: EngineParameters,
) -> float:
    """Multiplicative modifier on fatigue clearance rate.

    >1.0 = faster clearance (good recovery).
    <1.0 = slower clearance (poor recovery).

    Equation::

        m = clip_[m_min, m_max]( exp( beta_sleep * z_sleep + beta_stress * z_stress ) )

    with each ``z`` from :func:`_wellness_z` (0.0 for an unknown input, ADR-0049).
    Bounded to [recovery_clearance_min, recovery_clearance_max] = [0.60, 1.50] by
    default, hence strictly positive — the caller multiplies elapsed hours by it, and a
    zero or negative multiplier would freeze or reverse fatigue decay.

    Neutral (m = 1.0) when every input is either reported at
    ``RECOVERY_WELLNESS_CENTRE`` or unknown. Monotone non-decreasing in each reported
    input for non-negative betas. The exponent is bounded by
    ``clip * (|beta_sleep| + |beta_stress|)``; at default parameters the widest axis is
    ``cns`` (0.10 + 0.08) giving |exponent| <= 2.0 * 0.18 = 0.36, so ``math.exp`` stays
    in [0.698, 1.433] — numerically tame, and the outer clip is a guard on retuned
    betas rather than the usual path.

    Live effect of the ADR-0049 fix: a session logged with no check-in used to arrive
    here as the fabricated 5.0, i.e. z = -1 on both inputs, clearing cns fatigue at
    ``exp(-0.18) = 0.835`` — a silent ~16% under-clearance imposed on athletes purely
    for not filling in a form. It is now exactly 1.0.
    """
    beta = params.recovery_clearance_beta.get(axis, {"sleep": 0.06, "stress": 0.04})
    scale = params.recovery_zscore_scale
    z_sleep = _wellness_z(sleep_quality, scale)
    z_stress = _wellness_z(life_stress_inverse, scale)
    raw = math.exp(beta["sleep"] * z_sleep + beta["stress"] * z_stress)
    return max(params.recovery_clearance_min, min(params.recovery_clearance_max, raw))


def kalman_gain(prior_variance: float, measurement_variance: float) -> float:
    """Scalar Kalman gain in [0, 1): how much a measurement moves the estimate.

    Higher prior variance (less confident) or lower measurement variance (more
    trustworthy test) ⇒ larger correction. Used by the residual benchmark anchor
    (ADR-0034); confidence dynamics defined in ADR-0036.
    """
    pv = max(0.0, prior_variance)
    return pv / (pv + max(1e-9, measurement_variance))


def _grow_confidence_variance(
    confidence: CapacityConfidence,
    hours: float,
    p: EngineParameters,
) -> None:
    """Grow per-axis capacity variance with elapsed time (process noise).

    Training moves capacity mean but does not measure it, so only time passing
    increases uncertainty. Benchmarks reduce it. ADR-0036.
    """
    dt_days = hours / 24.0
    if dt_days <= 0.0:
        return
    for key in CapacityConfidence.KEYS:
        q = p.confidence_process_noise_per_day.get(key, 0.0025)
        max_v = p.confidence_max_variance.get(key, 1.5)
        v = getattr(confidence, key) + q * dt_days
        setattr(confidence, key, min(max_v, v))


def fatigue_impulse_from_dose(dose: StressDose) -> FatigueState:
    """Map legacy + six-vector dose into F increments (aligned with prior magnitudes)."""
    six = dose.dose_six
    return FatigueState(
        cns=min(100.0, dose.d_nm_central * 0.78 + six.intensity * 2.5 + six.skill * 2.0),
        muscular=min(100.0, dose.d_nm_peripheral * 0.88 + six.volume * 0.5),
        metabolic=min(100.0, dose.d_met_systemic * 0.92 + six.metabolic * 2.0),
        structural=min(100.0, dose.d_struct_damage * 0.52 + six.impact * 3.5),
        tendon=min(100.0, dose.d_struct_damage * 0.38 + six.impact * 2.8),
        grip=min(
            100.0,
            dose.d_struct_damage * 0.22 + dose.d_nm_peripheral * 0.12 + six.intensity * 1.2,
        ),
    )


def tissue_impulse_from_dose(dose: StressDose, log: WorkoutLog) -> dict[str, float]:
    movement = log.dominant_movement_pattern or (
        "run" if log.modality == "Running" else "mixed"
    )
    phi = default_phi_for_row(
        log.modality,
        movement,
        skill_demand=0.5,
        impact_level=0.65 if log.modality == "Running" else 0.5,
    )
    pt = phi["phi_tissue"]
    six = dose.dose_six
    scale = six.impact * 0.6 + six.volume * 0.04 + six.intensity * 0.45
    return {k: float(pt.get(k, 0.05)) * scale * 9.0 for k in TissueState.KEYS}


def _adaptation_efficiency(state: UnifiedStateVector, p: EngineParameters) -> float:
    """
    Compute adaptation efficiency multiplier [0.3, 1.0] based on mean fatigue.

    High fatigue suppresses adaptation: the body is in repair mode, not supercompensation.
    """
    f = state.fatigue_f
    f_values: list[float] = [float(getattr(f, k)) for k in FatigueState.KEYS]
    mean_f = sum(f_values) / max(1, len(f_values))

    thr = p.adapt_fatigue_suppress_threshold
    floor = p.adapt_fatigue_suppress_floor

    if mean_f <= thr:
        return 1.0
    # Linear suppression from threshold to 100 (at 100, efficiency = floor)
    excess = (mean_f - thr) / max(1.0, 100.0 - thr)
    return max(floor, 1.0 - excess * (1.0 - floor))


def _apply_adaptation_gains(
    s: UnifiedStateVector,
    dose: StressDose,
    p: EngineParameters,
) -> UnifiedStateVector:
    """
    Apply explicit per-axis capacity gains from dose.adaptation_contribution.

    Gains are scaled by:
    1. Per-axis adaptation coefficient (p.adapt_coef)
    2. Session efficiency (fatigue suppression)
    3. CNS fatigue penalty for skill axis

    Also applies cross-talk:
    - aerobic adaptation → nudges work_capacity
    - hypertrophy adaptation → slow support of max_strength
    """
    ac = dose.adaptation_contribution
    efficiency = _adaptation_efficiency(s, p)

    for key in ac.KEYS:
        signal = getattr(ac, key)
        if signal <= 0.0:
            continue

        coef = p.adapt_coef.get(key, 0.012)
        gain = signal * coef * efficiency

        # Concurrent-training interference (ADR-0037): routes through the
        # exponential suppression model (app/logic/interference.py).
        # CNS-on-skill is handled exclusively here (not the legacy
        # crosstalk_skill_suppressed_above_cns block, which was removed to
        # prevent double-suppression — each axis has a single CNS source).
        if key in ("max_strength", "power", "hypertrophy", "skill", "aerobic"):
            gain *= directional_interference_multiplier(key, s, p)

        cur = getattr(s.capacity_x, key)
        ceiling = capacity_ceiling(key)
        setattr(s.capacity_x, key, min(ceiling, cur + gain))

    # Cross-talk: aerobic improvement nudges work_capacity
    aerobic_gain = ac.aerobic * p.adapt_coef.get("aerobic", 0.015) * efficiency
    if aerobic_gain > 0:
        wc_xgain = aerobic_gain * p.crosstalk_aerobic_on_work_capacity / max(1e-6, p.adapt_coef.get("aerobic", 0.015))
        s.capacity_x.work_capacity = min(100.0, s.capacity_x.work_capacity + wc_xgain)

    # Cross-talk: hypertrophy slowly supports max_strength
    hyp_gain = ac.hypertrophy * p.adapt_coef.get("hypertrophy", 0.018) * efficiency
    if hyp_gain > 0:
        ms_xgain = hyp_gain * p.crosstalk_hypertrophy_on_max_strength / max(1e-6, p.adapt_coef.get("hypertrophy", 0.018))
        s.capacity_x.max_strength = min(100.0, s.capacity_x.max_strength + ms_xgain)

    return s


def _apply_detraining(s: UnifiedStateVector, hours: float, p: EngineParameters) -> None:
    """Erode each capacity for the elapsed time. No elapsed time, no detraining.

    First-order (exponential) decay: ``cur · exp(−rate · days)``.

    Physiological assumption: detraining is a first-order process — the rate of loss is
    proportional to what remains — which is the form the impulse-response family of models
    (Banister & Morton 1992 onward) uses for the decay of both fitness and fatigue components.
    Fatigue and tissue already decay this way here (``_exp_decay``); capacity was the odd one
    out with a LINEAR form, ``cur · (1 − rate · days)``.

    Why it matters beyond consistency: linear decay does not compose. Sixty daily updates gave
    ``(1 − r)^60`` while one sixty-day update gave ``1 − 60r``, so the same inactivity
    described a different athlete depending on how often the state happened to be updated.
    First-order decay composes exactly: ``exp(−r·a) · exp(−r·b) = exp(−r·(a+b))``.

    Before/after, 60 idle days at 0.0015/day: linear 0.9100, exponential 0.9139 — identical to
    first order in ``rate · days``, diverging only over long layoffs, where exponential also
    has the property that capacity can never decay through zero.
    """
    days = hours / 24.0
    if days <= 0:
        return
    for key in s.capacity_x.KEYS:
        rate = p.capacity_decay_per_day.get(key, 0.001)
        cur = getattr(s.capacity_x, key)
        setattr(s.capacity_x, key, max(0.0, cur * math.exp(-rate * days)))


#: Which transition chronology produced a state. Persisted in ``engine_state`` beside the
#: schema version (which it is deliberately NOT: the schema version gates a strict decoder,
#: this records model behaviour). Calibration must never compare observations produced under
#: two different orderings of decay and adaptation.
#:   v0-chrono-1  (implicit, every state before this field existed) detraining ran AFTER
#:                adaptation, was linear in days, and idle updates earned metabolic cross-talk
#:   v0-chrono-2  detraining runs with the other elapsed-time decay BEFORE today's stimulus,
#:                is first-order (composes over split updates), and cross-talk needs a stimulus
STATE_UPDATE_MODEL_VERSION = "v0-chrono-2"


def update_athlete_state(
    prev_state: UnifiedStateVector,
    dose: StressDose,
    time_delta: timedelta,
    log: WorkoutLog,
) -> UnifiedStateVector:
    hours = time_delta.total_seconds() / 3600.0
    if hours < 0:
        hours = 0.0

    p = default_parameters()
    s = prev_state.model_copy(deep=True)

    # --- 1. Fatigue decay (Λ) with recovery-modulated clearance rate ---
    # Multiplier >1 (good sleep/low stress) → effective hours larger → faster decay.
    # Multiplier <1 (poor sleep/high stress) → effective hours smaller → slower decay.
    for key in FatigueState.KEYS:
        tau = p.tau_fatigue_hours[key]
        m = recovery_clearance_multiplier(key, log.sleep_quality, log.life_stress_inverse, p)
        v = getattr(s.fatigue_f, key)
        setattr(s.fatigue_f, key, _exp_decay(v, hours * m, tau))

    # --- 2. Tissue decay (Γ) — accumulated stress eases ---
    for key in TissueState.KEYS:
        tau = p.tau_tissue_hours[key]
        v = getattr(s.tissue_t, key)
        setattr(s.tissue_t, key, _exp_decay(v, hours, tau))

    # --- 2b. Detraining: capacities erode with the elapsed time BEFORE this session ---
    # Chronology (phase 1.3): old state -> elapsed-time decay -> today's stress -> today's
    # adaptation -> new state. Detraining belongs with the other elapsed-time terms because
    # the idle days happened BEFORE the session. It used to run after the adaptation gains
    # (the old step 5b), so a session logged after N idle days had its brand-new gain
    # decayed by those N days — time in which that adaptation did not yet exist. Running it
    # here also means today's response is computed on the DECAYED state, which is the
    # substrate the athlete actually brought to the session.
    _apply_detraining(s, hours, p)

    # --- 3. Impulses from training dose ---
    d_f = fatigue_impulse_from_dose(dose)
    for key in FatigueState.KEYS:
        v = getattr(s.fatigue_f, key) + getattr(d_f, key)
        setattr(s.fatigue_f, key, max(0.0, min(100.0, v)))

    d_t = tissue_impulse_from_dose(dose, log)
    for key in TissueState.KEYS:
        v = getattr(s.tissue_t, key) + d_t[key]
        setattr(s.tissue_t, key, max(0.0, min(100.0, v)))

    # --- 4. Signaling + slow structural capacity (Banister-style nudge) ---
    s.s_struct_signal = _exp_decay(s.s_struct_signal, hours, cross_talk.TAU_SIGNAL)
    s.s_struct_signal += dose.d_struct_signal
    s.s_struct_signal = max(0.0, s.s_struct_signal)

    # Structural capacity gain proportional to THIS session's structural signal — so
    # endurance (which carries ~no signal) doesn't drive strength — gated by concurrent
    # interference (metabolic load blunts strength/hypertrophy adaptation, ADR-0037).
    if dose.d_struct_signal > 0.0:
        struct_drive = math.log1p(dose.d_struct_signal)
        # Concurrent interference is the single ADR-0037 authority
        # (directional_interference_multiplier, app/logic/interference.py):
        # apply the per-axis exponential multiplier to each structural bump
        # rather than one shared legacy-linear scalar.
        s.capacity_x.hypertrophy = min(
            100.0,
            s.capacity_x.hypertrophy
            + p.capacity_hypertrophy_bump
            * struct_drive
            * directional_interference_multiplier("hypertrophy", s, p),
        )
        s.capacity_x.max_strength = min(
            100.0,
            s.capacity_x.max_strength
            + p.capacity_struct_bump
            * struct_drive
            * directional_interference_multiplier("max_strength", s, p),
        )

    # --- 5. Explicit adaptation gains (new v2 path) ---
    s = _apply_adaptation_gains(s, dose, p)

    # --- 6. Legacy metabolic cross-talk (preserved from v1) ---
    # Gated on a training STIMULUS (phase 1.3). It used to run on every update, driven by
    # residual metabolic fatigue, so an idle update granted work capacity — and twenty idle
    # updates granted more than one update covering the same twenty days. Adaptation needs a
    # stimulus; recomputing the state is not one. With a stimulus the arithmetic is unchanged
    # (residual + today's impulse, exactly as before), so every real session is unaffected.
    # Whether residual fatigue should feed this term at all is a modelling question left to
    # calibration (phase 8), not changed here.
    if d_f.metabolic > 0.0:
        wc_gain = p.crosstalk_metabolic_on_work_capacity * min(s.fatigue_f.metabolic * 0.015, 0.4)
        s.capacity_x.work_capacity = min(100.0, s.capacity_x.work_capacity + wc_gain)

    # --- 7. Legacy mirrors ---
    legacy = sync_legacy_from_vectors(s.capacity_x, s.fatigue_f, s.tissue_t)
    s.c_met_aerobic = legacy["c_met_aerobic"]
    s.c_nm_force = legacy["c_nm_force"]
    s.c_struct = legacy["c_struct"]
    s.b_met_anaerobic = legacy["b_met_anaerobic"]
    s.f_met_systemic = legacy["f_met_systemic"]
    s.f_nm_peripheral = legacy["f_nm_peripheral"]
    s.f_nm_central = legacy["f_nm_central"]
    s.f_struct_damage = legacy["f_struct_damage"]

    # --- 8. Confidence: uncertainty about capacity accrues with elapsed time ---
    _grow_confidence_variance(s.capacity_confidence, hours, p)

    s.timestamp = prev_state.timestamp + time_delta
    return s
