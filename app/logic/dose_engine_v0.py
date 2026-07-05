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
from app.schemas.engine_vectors import AdaptationContribution, StressDoseSix
from app.schemas.workouts import ExerciseEntry, StressDose, WorkoutLog

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
# Helpers
# ---------------------------------------------------------------------------

def _infer_movement_pattern(log: WorkoutLog) -> str:
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


def _external_intensity_from_reps(
    reps: float | None,
    rir: float | None,
) -> float:
    """External load as a fraction of 1RM, estimated from reps + reps-in-reserve (ADR-0039).

    This is the *external* intensity term I — independent of internal effort F: a heavy
    triple and a set of twelve to failure carry different loads at the same effort.
    Epley: %1RM = 1 / (1 + reps_to_failure / 30). Falls back to a neutral 1.0 when reps
    are unknown (so the dose degrades to effort-only rather than squaring effort).
    """
    if reps is None:
        return 1.0
    reserve = rir if rir is not None else 0.0
    reps_to_failure = max(1.0, float(reps) + float(reserve))
    pct = 1.0 / (1.0 + reps_to_failure / 30.0)
    return max(0.3, min(1.0, pct))


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
    log: WorkoutLog, params: EngineParameters | None = None
) -> StressDose:
    """
    Compute session stress dose from a WorkoutLog.

    When log.exercises is populated with resolved phi vectors, the dose
    reflects the actual exercise selection. Otherwise falls back to
    modality-level defaults (legacy behavior, fully preserved).

    Returns StressDose including:
    - dose_six: 6-axis session dose
    - adaptation_contribution: per-capacity-axis adaptation signal
    - legacy scalar channels (backward compat)
    """
    p = params or default_parameters()
    movement = _infer_movement_pattern(log)

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

    # ADR-0039: separate external load (I) from internal effort (F). A session log has
    # no per-exercise load, so I=1 (effort-only via F) — this stops the old double-count
    # where intensity (RPE) and proximity-to-failure (also RPE-derived) were multiplied.
    external_intensity = 1.0
    base = (
        w_phi
        * math.log1p(V)
        * (external_intensity ** p.dose_alpha)
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
    )


# ---------------------------------------------------------------------------
# Exercise-level dose building
# ---------------------------------------------------------------------------

def _build_exercise_doses(log: WorkoutLog, p: EngineParameters) -> list[_ExerciseDose]:
    """Build per-exercise dose bundles, then return for aggregation."""
    doses: list[_ExerciseDose] = []
    for entry in log.exercises:
        phi_pack = _phi_for_entry(entry, log.modality)
        vol_proxy = _entry_volume_proxy(entry, p)
        # ADR-0039: external load from reps+RIR (I), independent of effort (F).
        external_intensity = _external_intensity_from_reps(entry.reps, entry.avg_rir)
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
            * (external_intensity ** p.dose_alpha)
            * (Delta ** p.dose_beta)
            * (N ** p.dose_gamma)
            * (fp ** p.dose_rho)
        )

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
