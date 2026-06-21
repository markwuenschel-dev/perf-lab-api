"""
Current production athlete state evolution engine.

Handles:
- Multi-timescale fatigue decay
- Tissue load accumulation
- Capacity adaptation driven by AdaptationContribution vectors
- Cross-talk between domains
- Benchmark observation assimilation with correct timestamps

This is the preferred module for state updates. See also:
- `app.logic.state_dynamics`
- `app.logic.cross_talk`
- `app.services.state_service`
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.engine.parameters import EngineParameters, default_parameters
from app.engine.phi_table import default_phi_for_row
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic import cross_talk
from app.schemas.engine_vectors import CapacityConfidence, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog

_VECTOR_ATTR = {"capacity": "capacity_x", "fatigue": "fatigue_f", "tissue": "tissue_t"}


def _capacity_ceiling(key: str) -> float:
    return 650.0 if key == "aerobic" else 100.0


def _read_axis(state: UnifiedStateVector, vector: str, key: str) -> float:
    sub = getattr(state, _VECTOR_ATTR[vector])
    return float(getattr(sub, key))


def _write_axis(state: UnifiedStateVector, vector: str, key: str, value: float) -> None:
    sub = getattr(state, _VECTOR_ATTR[vector])
    cap = _capacity_ceiling(key) if vector == "capacity" else 100.0
    setattr(sub, key, max(0.0, min(cap, value)))


def _mapping_delta(
    mapping: Any,
    signal: float,
    observation_weight: float,
) -> float:
    cfg = mapping.config or {}
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
) -> None:
    """Signed, confidence-scaled residual correction of a capacity axis (ADR-0034).

    The model's expectation for the axis is its current value (as a fraction of the
    axis ceiling); the measurement is ``score01``. We move the axis toward the
    measurement by a Kalman gain set by how unsure we are (low confidence → big move),
    then shrink that axis's variance. A below-expectation test pulls the axis *down*.
    """
    key = mapping.target_key
    try:
        cur = _read_axis(s, "capacity", key)
    except AttributeError:
        return
    ceiling = _capacity_ceiling(key)
    expected01 = cur / ceiling if ceiling > 0 else 0.0
    residual01 = score01 - expected01
    weight = max(0.0, float(mapping.coefficient))  # mapping informativeness, ~[0, 1]
    meas_var = p.confidence_measured_variance / max(0.1, float(observation_weight))
    prior_var = float(getattr(s.capacity_confidence, key, 1.0))
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
            _apply_capacity_residual(s, m, score01, observation_weight, p)
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
    """Grow per-axis capacity variance with elapsed time (process noise), in place.

    Training moves the capacity *mean* but does not *measure* it, so it does not
    increase confidence — only time passing (uncertainty accrues) and, elsewhere,
    benchmarks (which reduce it). See ADR-0036.
    """
    growth = p.confidence_process_noise_per_day * (hours / 24.0)
    if growth <= 0.0:
        return
    for key in CapacityConfidence.KEYS:
        v = getattr(confidence, key) + growth
        setattr(confidence, key, min(p.confidence_max_variance, v))


def _fatigue_impulse_from_dose(dose: StressDose) -> FatigueState:
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


def _tissue_impulse_from_dose(dose: StressDose, log: WorkoutLog) -> dict[str, float]:
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

        # Skill adaptation is additionally suppressed under high CNS fatigue
        if key == "skill" and s.fatigue_f.cns > p.crosstalk_skill_suppressed_above_cns:
            cns_excess = (s.fatigue_f.cns - p.crosstalk_skill_suppressed_above_cns) / 45.0
            gain *= max(0.5, 1.0 - cns_excess * 0.5)

        cur = getattr(s.capacity_x, key)
        ceiling = _capacity_ceiling(key)
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

    # --- 1. Fatigue decay (Λ) ---
    for key in FatigueState.KEYS:
        tau = p.tau_fatigue_hours[key]
        v = getattr(s.fatigue_f, key)
        setattr(s.fatigue_f, key, _exp_decay(v, hours, tau))

    # --- 2. Tissue decay (Γ) — accumulated stress eases ---
    for key in TissueState.KEYS:
        tau = p.tau_tissue_hours[key]
        v = getattr(s.tissue_t, key)
        setattr(s.tissue_t, key, _exp_decay(v, hours, tau))

    # --- 3. Recovery Ω (sleep / life stress) ---
    omega = (
        p.recovery_sleep_scale * max(0.0, 5.0 - log.sleep_quality) * hours
        + p.recovery_stress_scale * max(0.0, 5.0 - log.life_stress_inverse) * hours
    )
    for key in FatigueState.KEYS:
        v = getattr(s.fatigue_f, key)
        setattr(s.fatigue_f, key, max(0.0, v - omega * 0.18))

    # --- 4. Impulses from training dose ---
    d_f = _fatigue_impulse_from_dose(dose)
    for key in FatigueState.KEYS:
        v = getattr(s.fatigue_f, key) + getattr(d_f, key)
        setattr(s.fatigue_f, key, max(0.0, min(100.0, v)))

    d_t = _tissue_impulse_from_dose(dose, log)
    for key in TissueState.KEYS:
        v = getattr(s.tissue_t, key) + d_t[key]
        setattr(s.tissue_t, key, max(0.0, min(100.0, v)))

    # --- 5. Signaling + slow structural capacity (Banister-style nudge) ---
    s.s_struct_signal = _exp_decay(s.s_struct_signal, hours, cross_talk.TAU_SIGNAL)
    s.s_struct_signal += dose.d_struct_signal
    s.s_struct_signal = max(0.0, s.s_struct_signal)

    if s.s_struct_signal > p.capacity_signal_threshold:
        s.capacity_x.hypertrophy = min(
            100.0,
            s.capacity_x.hypertrophy + p.capacity_hypertrophy_bump,
        )
        s.capacity_x.max_strength = min(
            100.0,
            s.capacity_x.max_strength + p.capacity_struct_bump,
        )
        s.s_struct_signal *= 0.85

    # --- 6. Explicit adaptation gains (new v2 path) ---
    s = _apply_adaptation_gains(s, dose, p)

    # --- 7. Legacy metabolic cross-talk (preserved from v1) ---
    wc_gain = p.crosstalk_metabolic_on_work_capacity * min(s.fatigue_f.metabolic * 0.015, 0.4)
    s.capacity_x.work_capacity = min(100.0, s.capacity_x.work_capacity + wc_gain)

    # --- 8. Legacy mirrors ---
    legacy = sync_legacy_from_vectors(s.capacity_x, s.fatigue_f, s.tissue_t)
    s.c_met_aerobic = legacy["c_met_aerobic"]
    s.c_nm_force = legacy["c_nm_force"]
    s.c_struct = legacy["c_struct"]
    s.b_met_anaerobic = legacy["b_met_anaerobic"]
    s.f_met_systemic = legacy["f_met_systemic"]
    s.f_nm_peripheral = legacy["f_nm_peripheral"]
    s.f_nm_central = legacy["f_nm_central"]
    s.f_struct_damage = legacy["f_struct_damage"]

    # --- 9. Confidence: uncertainty about capacity accrues with elapsed time ---
    _grow_confidence_variance(s.capacity_confidence, hours, p)

    s.timestamp = prev_state.timestamp + time_delta
    return s
