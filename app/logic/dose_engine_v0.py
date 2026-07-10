"""
Current production stress dose engine (v0.3+ refined math model).

Converts a `WorkoutLog` (with per-exercise data) into a rich `StressDose`
containing:
- `dose_six`: 6-axis dose vector
- `adaptation_contribution`: per-capacity adaptation signals
- Legacy scalar channels for backward compatibility

This is the preferred implementation. The older dict-based version lives in
`app.logic.dose_engine` (deprecated).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.engine.parameters import EngineParameters, default_parameters
from app.engine.phi_table import default_phi_for_row
from app.logic import strength_calibration as sc
from app.logic.strength_calibration import CalibrationResult
from app.schemas.engine_vectors import AdaptationContribution, StressDoseSix
from app.schemas.workouts import (
    ExerciseEntry,
    ExternalIntensity,
    IntensityContribution,
    StressDose,
    WorkoutLog,
)

# ADR-0054 routing caveat carried on every Model A dose: a session-scalar intensity
# flows through the aggregate-φ / derived-modality shaping path, so a hard accessory
# partially inherits the session's shape. Per-exercise φ routing is ADR-0054's job.
_ADR_0054_KNOWN_LIMITATION = (
    "session-scalar external intensity (ADR-0039 Model A): a single session I shapes "
    "every exercise via the aggregate-φ path; per-exercise dose routing is ADR-0054."
)

# ---------------------------------------------------------------------------
# Internal per-exercise dose bundle (not exposed in API)
# ---------------------------------------------------------------------------

@dataclass
class _ExerciseDose:
    """Aggregation bucket for a single exercise entry."""
    base: float
    phi_adapt: dict[str, float]
    phi_fatigue: dict[str, float]
    phi_tissue: dict[str, float]
    energy_mix: dict[str, float]
    volume_weight: float   # relative contribution weight for aggregation


# ---------------------------------------------------------------------------
# ADR-0039 Model A: session external intensity assembly
# ---------------------------------------------------------------------------

@dataclass
class SetIntensitySample:
    """One logged set's external-intensity reading plus its denominator provenance.

    Built by the service layer (which owns the DB read of the pre-log e1RM) and
    aggregated by :func:`build_session_external_intensity` into the session scalar.
    """

    exercise_id: int | None
    exercise_name: str | None
    result: CalibrationResult          # from strength_calibration.external_intensity_for_set
    weight: float                      # w = reps · load (0 for non-loaded sets)
    e1rm_source: str | None = None
    e1rm_value_semantics: str | None = None
    e1rm_observation_id: int | None = None


@dataclass
class _ExerciseIntensityAgg:
    exercise_id: int | None
    exercise_name: str | None
    weighted_value: float = 0.0
    weighted_conf: float = 0.0
    weight: float = 0.0
    # Provenance from the heaviest-weighted sample of this exercise.
    top_weight: float = 0.0
    source: str = sc.SRC_NEUTRAL_MISSING
    e1rm_denominator_kg: float | None = None
    e1rm_source: str | None = None
    e1rm_value_semantics: str | None = None
    e1rm_observation_id: int | None = None


def build_session_external_intensity(
    samples: list[SetIntensitySample],
) -> ExternalIntensity:
    """Roll per-set intensities up to the session scalar (set → exercise → session).

    Weights by ``w = reps · load`` at each level (ADR-0039). Only loaded sets carry
    external intensity; non-loaded sets (weight 0) do not dilute it. With no loaded
    set the session degrades to a labeled neutral ``I = 1.0`` (``neutral_missing``).
    """
    contributing = [s for s in samples if s.weight > 0]
    if not contributing:
        return _neutral_external_intensity()

    # Group by exercise (id when present, else name), weighted by w = reps · load.
    by_ex: dict[Any, _ExerciseIntensityAgg] = {}
    all_sources: set[str] = set()
    for s in contributing:
        key = ("id", s.exercise_id) if s.exercise_id is not None else ("name", s.exercise_name)
        agg = by_ex.get(key)
        if agg is None:
            agg = _ExerciseIntensityAgg(s.exercise_id, s.exercise_name)
            by_ex[key] = agg
        agg.weighted_value += s.result.value * s.weight
        agg.weighted_conf += s.result.confidence * s.weight
        agg.weight += s.weight
        all_sources.add(s.result.source)
        if s.weight >= agg.top_weight:
            agg.top_weight = s.weight
            agg.source = s.result.source
            agg.e1rm_denominator_kg = s.result.e1rm_pre
            agg.e1rm_source = s.e1rm_source
            agg.e1rm_value_semantics = s.e1rm_value_semantics
            agg.e1rm_observation_id = s.e1rm_observation_id

    contributions: list[IntensityContribution] = []
    session_wv = 0.0
    session_wc = 0.0
    session_w = 0.0
    dominant_source = sc.SRC_NEUTRAL_MISSING
    dominant_weight = -1.0

    for agg in by_ex.values():
        ex_value = agg.weighted_value / agg.weight
        ex_conf = agg.weighted_conf / agg.weight
        contributions.append(
            IntensityContribution(
                exercise_id=agg.exercise_id,
                exercise_name=agg.exercise_name,
                external_intensity=round(ex_value, 4),
                source=agg.source,
                confidence=round(ex_conf, 4),
                weight=round(agg.weight, 2),
                e1rm_denominator_kg=agg.e1rm_denominator_kg,
                e1rm_source=agg.e1rm_source,
                e1rm_value_semantics=agg.e1rm_value_semantics,
                e1rm_observation_id=agg.e1rm_observation_id,
            )
        )
        session_wv += ex_value * agg.weight
        session_wc += ex_conf * agg.weight
        session_w += agg.weight
        if agg.weight > dominant_weight:
            dominant_weight = agg.weight
            dominant_source = agg.source

    value = session_wv / session_w
    confidence = session_wc / session_w
    source = "aggregate" if len(all_sources) > 1 else dominant_source
    return ExternalIntensity(
        value=round(value, 4),
        source=source,
        model_version=sc.MODEL_VERSION,
        confidence=round(confidence, 4),
        fallback_path=dominant_source,
        known_limitation=_ADR_0054_KNOWN_LIMITATION,
        contributions=contributions,
    )


def _neutral_external_intensity() -> ExternalIntensity:
    """The honest ``I = 1.0`` for a session with no external-load signal.

    Labeled ``neutral_missing`` with zero confidence so it never reads as "moderate
    intensity" — it means "unknown, so the dose degrades to effort-only" (ADR-0039).
    """
    return ExternalIntensity(
        value=1.0,
        source=sc.SRC_NEUTRAL_MISSING,
        model_version=sc.MODEL_VERSION,
        confidence=0.0,
        fallback_path="session_no_external_load",
        known_limitation=None,
        contributions=[],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def infer_movement_pattern(log: WorkoutLog) -> str:
    if log.dominant_movement_pattern:
        return log.dominant_movement_pattern
    if log.modality == "Running":
        return "run"
    return "mixed"


def _phi_for_entry(entry: ExerciseEntry, session_modality: str) -> dict[str, Any]:
    """
    Resolve phi vectors for one exercise entry.

    Priority:
      1. Resolved phi vectors on the entry (populated by service layer from DB row)
      2. Entry's own modality/movement_pattern fields (if DB row populated them)
      3. Session modality fallback via default_phi_for_row
    """
    if entry.phi_adapt:
        return {
            "phi_adapt": entry.phi_adapt,
            "phi_fatigue": entry.phi_fatigue or {},
            "phi_tissue": entry.phi_tissue or {},
            "energy_mix": entry.energy_mix or {},
        }
    # Fall back to defaults using entry-level metadata if available
    modality = entry.modality or session_modality
    movement = entry.movement_pattern or "mixed"
    skill = entry.skill_demand if entry.skill_demand is not None else 0.5
    impact = entry.impact_level if entry.impact_level is not None else 0.5
    return default_phi_for_row(modality, movement, skill, impact)


def _entry_volume_proxy(entry: ExerciseEntry, p: EngineParameters) -> float:
    """Compute a volume proxy for a single exercise entry."""
    sets = entry.sets or 3.0
    reps = entry.reps or 8.0
    load = entry.load_kg or 0.0
    dur = entry.duration_seconds or 0.0
    dist = entry.distance_meters or 0.0

    w = p.dose_entry_volume_proxy_weights
    # Weight × reps × sets proxy
    vol_load = sets * reps * load
    # Add time / distance terms
    return (
        vol_load * w["volume_load"]
        + dur / w["duration_divisor"]
        + dist / w["distance_divisor"]
        + sets * reps * w["sets_reps"]
    )


def _entry_intensity(entry: ExerciseEntry, session_rpe: float) -> float:
    """Intensity 0–1 for a single entry; falls back to session RPE."""
    if entry.avg_rpe is not None:
        return entry.avg_rpe / 10.0
    return session_rpe / 10.0


def _entry_failure_proximity(entry: ExerciseEntry, session_rpe: float) -> float:
    """Proximity to failure 0–1 (internal effort, F)."""
    if entry.avg_rir is not None:
        return max(0.15, min(1.0, (10.0 - entry.avg_rir) / 10.0))
    return max(0.2, session_rpe / 10.0)


def _aggregate_phi(
    exercise_doses: list[_ExerciseDose],
) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, float]]:
    """
    Volume-weighted aggregation of phi vectors across exercises.

    Returns (phi_adapt, phi_fatigue, phi_tissue, energy_mix).
    """
    total_w = sum(d.volume_weight for d in exercise_doses)
    if total_w <= 0:
        total_w = 1.0

    agg_adapt: dict[str, float] = {}
    agg_fatigue: dict[str, float] = {}
    agg_tissue: dict[str, float] = {}
    agg_em: dict[str, float] = {}

    for d in exercise_doses:
        w = d.volume_weight / total_w
        for k, v in d.phi_adapt.items():
            agg_adapt[k] = agg_adapt.get(k, 0.0) + v * w
        for k, v in d.phi_fatigue.items():
            agg_fatigue[k] = agg_fatigue.get(k, 0.0) + v * w
        for k, v in d.phi_tissue.items():
            agg_tissue[k] = agg_tissue.get(k, 0.0) + v * w
        for k, v in d.energy_mix.items():
            agg_em[k] = agg_em.get(k, 0.0) + v * w

    return agg_adapt, agg_fatigue, agg_tissue, agg_em


def _compute_adaptation_contribution(
    phi_adapt: dict[str, float],
    base: float,
) -> AdaptationContribution:
    """
    Map phi_adapt weights + base dose → per-axis adaptation signal.

    phi_adapt keys use the vocabulary from domain_vocab.PHI_ADAPT_TO_CAPACITY.
    We normalize here so the output uses CapacityState field names.
    """
    from app.logic.domain_vocab import PHI_ADAPT_TO_CAPACITY

    ac: dict[str, float] = dict.fromkeys(AdaptationContribution.KEYS, 0.0)
    for phi_key, weight in phi_adapt.items():
        cap_key = PHI_ADAPT_TO_CAPACITY.get(phi_key)
        if cap_key and cap_key in ac:
            ac[cap_key] += weight * base
    return AdaptationContribution(**ac)


# ---------------------------------------------------------------------------
# Primary entry point
# ---------------------------------------------------------------------------

def calculate_stress_dose(
    log: WorkoutLog,
    params: EngineParameters | None = None,
    external_intensity: ExternalIntensity | None = None,
) -> StressDose:
    """
    Compute session stress dose from a WorkoutLog.

    When log.exercises is populated with resolved phi vectors, the dose
    reflects the actual exercise selection. Otherwise falls back to
    modality-level defaults (legacy behavior, fully preserved).

    ``external_intensity`` (ADR-0039 Model A) is the session-scalar load-relative-to-
    capacity ``I`` computed by the service layer from logged sets and the athlete's
    pre-log e1RM. When ``None`` — every path without per-set load — a labeled neutral
    ``I = 1.0`` is used and recorded, so the dose degrades to effort-only rather than
    silently pretending the load was moderate.

    Returns StressDose including:
    - dose_six: 6-axis session dose
    - adaptation_contribution: per-capacity-axis adaptation signal
    - external_intensity: the ``I`` that shaped this dose, with provenance
    - legacy scalar channels (backward compat)
    """
    p = params or default_parameters()
    ext = external_intensity or _neutral_external_intensity()
    movement = infer_movement_pattern(log)

    # ------------------------------------------------------------------
    # Resolve phi pack: exercise-aware or modality fallback
    # ------------------------------------------------------------------
    if log.exercises:
        exercise_doses = _build_exercise_doses(log, p)
        phi_adapt, phi_fatigue, phi_tissue, energy_mix = _aggregate_phi(exercise_doses)
    else:
        phi_pack = default_phi_for_row(
            log.modality,
            movement,
            skill_demand=0.55 if log.modality in ("Power", "Mixed") else 0.45,
            impact_level=0.75 if log.modality == "Running" else 0.55,
        )
        phi_adapt = phi_pack["phi_adapt"]
        phi_fatigue = phi_pack["phi_fatigue"]
        energy_mix = phi_pack["energy_mix"]

    # ------------------------------------------------------------------
    # Session-level volume proxy (always from session fields)
    # ------------------------------------------------------------------
    sets = log.estimated_sets or max(3.0, log.duration_minutes / 12.0)
    vol_load = log.total_volume_load or 0.0
    vw = p.dose_volume_weights
    V = vw["duration"] * log.duration_minutes + vw["volume_load"] * vol_load + vw["sets"] * sets

    intensity_u = log.session_rpe / 10.0
    density_raw = min(
        p.dose_delta_cap,
        log.duration_minutes
        / max(p.dose_delta_min_divisor, sets * p.dose_delta_sets_multiplier),
    )
    Delta = max(p.dose_delta_floor, density_raw)
    N = max(p.dose_novelty_floor, log.novelty)
    if log.avg_rir is not None:
        F = max(0.15, min(1.0, (10.0 - log.avg_rir) / 10.0))
    else:
        F = max(0.2, intensity_u)

    w_phi = max(p.dose_w_phi_floor, sum(phi_fatigue.values()) / max(1, len(phi_fatigue)))

    # ADR-0039 Model A: separate external load (I) from internal effort (F). The
    # service layer computes a session-scalar I = load / e1RM_pre (weighted set →
    # exercise → session); absent per-set load it is a labeled neutral 1.0 (effort-
    # only via F). This replaces the old hardcoded 1.0 and stops the double-count
    # where intensity (RPE) and proximity-to-failure (also RPE-derived) were squared.
    base = (
        w_phi
        * math.log1p(V)
        * (ext.value ** p.dose_alpha)
        * (Delta ** p.dose_beta)
        * (N ** p.dose_gamma)
        * (F ** p.dose_rho)
    )

    # ------------------------------------------------------------------
    # Build 6-axis dose vector (modality-shaped)
    # ------------------------------------------------------------------
    six = _shape_six(base, log.modality, intensity_u, Delta, F, phi_adapt, energy_mix, p)

    # Human-factor gain
    hf_ref = p.dose_human_factor_reference
    hf_slope = p.dose_human_factor_slope
    sleep_penalty = 1.0 + max(0.0, (hf_ref - log.sleep_quality) * hf_slope)
    life_penalty = 1.0 + max(0.0, (hf_ref - log.life_stress_inverse) * hf_slope)
    global_gain = sleep_penalty * life_penalty
    six = six.scaled(global_gain)

    # ------------------------------------------------------------------
    # Adaptation contribution
    # ------------------------------------------------------------------
    adapt_contrib = _compute_adaptation_contribution(phi_adapt, base)
    adapt_contrib = adapt_contrib.scaled(1.0 / global_gain)  # fatigue penalty → lower gain

    # ------------------------------------------------------------------
    # Legacy channels
    # ------------------------------------------------------------------
    d_met_systemic = six.metabolic * 14.0 + six.density * 6.0
    d_nm_peripheral = six.volume * 0.55 + six.intensity * 9.0
    d_nm_central = six.intensity * 11.0 + six.skill * 7.0
    d_struct_damage = six.impact * 18.0 + six.volume * 0.35
    if log.modality == "Running":
        # Endurance drives ~no hypertrophy/strength structural (mTOR) signal, so it must
        # not feed the Banister strength bump — otherwise concurrent endurance would
        # *raise* strength (the opposite of interference, ADR-0037).
        d_struct_signal = 0.0
    elif log.avg_rir is not None and log.avg_rir <= 3:
        d_struct_signal = log.duration_minutes * 1.5 + six.intensity * 4.0
    else:
        d_struct_signal = log.duration_minutes * 0.25 + six.intensity * 1.0

    return StressDose(
        dose_six=six,
        adaptation_contribution=adapt_contrib,
        d_met_systemic=max(0.0, d_met_systemic),
        d_nm_peripheral=max(0.0, d_nm_peripheral),
        d_nm_central=max(0.0, d_nm_central),
        d_struct_damage=max(0.0, d_struct_damage),
        d_struct_signal=max(0.0, d_struct_signal),
        external_intensity=ext,
    )


# ---------------------------------------------------------------------------
# Exercise-level dose building
# ---------------------------------------------------------------------------

def exercise_base_bundle(
    entry: ExerciseEntry, log: WorkoutLog, p: EngineParameters
) -> tuple[float, dict[str, Any], float]:
    """The one intensity-free per-exercise base, its φ pack, and its volume proxy.

    Returns ``(base, phi_pack, vol_proxy)`` where
    ``base = w_phi · log1p(vol_proxy) · Δ^β · N^γ · fp^ρ`` — no intensity term (ADR-0039
    Model A applies ``I`` once at the session level). This is the single source for the
    per-exercise base, shared by the aggregate-φ dose path (``_build_exercise_doses``)
    and the shadow per-exercise routing (``app.logic.dose_routing``, ADR-0054) so the
    two never drift onto divergent base formulas.
    """
    phi_pack = _phi_for_entry(entry, log.modality)
    vol_proxy = _entry_volume_proxy(entry, p)
    fp = _entry_failure_proximity(entry, log.session_rpe)

    N = max(p.dose_novelty_floor, log.novelty)
    Delta = max(
        p.dose_delta_floor,
        min(p.dose_delta_cap, (entry.sets or 3) / max(1.0, (entry.rest_seconds or 120) / 60)),
    )
    w_phi = max(
        p.dose_w_phi_floor,
        sum(phi_pack["phi_fatigue"].values()) / max(1, len(phi_pack["phi_fatigue"])),
    )
    base = (
        w_phi
        * math.log1p(max(0.1, vol_proxy))
        * (Delta ** p.dose_beta)
        * (N ** p.dose_gamma)
        * (fp ** p.dose_rho)
    )
    return base, phi_pack, vol_proxy


def _build_exercise_doses(log: WorkoutLog, p: EngineParameters) -> list[_ExerciseDose]:
    """Build per-exercise dose bundles, then return for aggregation."""
    doses: list[_ExerciseDose] = []
    for entry in log.exercises:
        base, phi_pack, vol_proxy = exercise_base_bundle(entry, log, p)
        doses.append(
            _ExerciseDose(
                base=base,
                phi_adapt=phi_pack["phi_adapt"],
                phi_fatigue=phi_pack["phi_fatigue"],
                phi_tissue=phi_pack["phi_tissue"],
                energy_mix=phi_pack["energy_mix"],
                volume_weight=max(0.1, vol_proxy),
            )
        )
    return doses


# ---------------------------------------------------------------------------
# Six-axis shaping (modality-specific distribution of base dose)
# ---------------------------------------------------------------------------

def _shape_six(
    base: float,
    modality: str,
    intensity_u: float,
    Delta: float,
    F: float,
    phi_adapt: dict[str, float],
    energy_mix: dict[str, float],
    p: EngineParameters | None = None,
) -> StressDoseSix:
    p = p or default_parameters()
    em_aerobic = energy_mix.get("aerobic", 0.33)
    em_glycolytic = energy_mix.get("glycolytic", 0.33)
    skill_phi = phi_adapt.get("skill", 0.15)
    shape = p.dose_shape_six_by_modality

    if modality == "Running":
        m = shape["Running"]
        return StressDoseSix(
            volume=base * m["volume"] * (em_aerobic + 0.4),
            intensity=base * m["intensity"] * intensity_u,
            density=base * m["density"] * Delta,
            impact=base * m["impact"] * max(intensity_u, 0.4),
            skill=base * m["skill"],
            metabolic=base * m["metabolic"] * (em_aerobic + em_glycolytic),
        )
    if modality in ("Strength", "Hypertrophy", "Power", "Mixed"):
        m = shape["strength"]
        return StressDoseSix(
            volume=base * m["volume"],
            intensity=base * m["intensity"] * intensity_u,
            density=base * m["density"] * Delta,
            impact=base * m["impact"] * F,
            skill=base * m["skill"] * max(skill_phi, 0.1),
            metabolic=base * m["metabolic"] * (em_glycolytic + 0.2),
        )
    m = shape["default"]
    return StressDoseSix(
        volume=base * m["volume"],
        intensity=base * m["intensity"] * intensity_u,
        density=base * m["density"] * Delta,
        impact=base * m["impact"],
        skill=base * m["skill"],
        metabolic=base * m["metabolic"],
    )
