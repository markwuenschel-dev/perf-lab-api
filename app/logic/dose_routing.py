"""Per-exercise dose routing — Model B, shadow-only (ADR-0054).

Where Model A ([ADR-0039](../../docs/adr/0039-dose-law-external-load-vs-effort.md)) shapes
one session ``base`` by the session-aggregate φ and the derived session modality, Model B
sums **per-exercise routed contributions** — each exercise's dose ``D_i`` landing through
**its own** φ vectors — so accessory / mixed-modality work stops bleeding through the
session shape, and tissue stops being routed off the (lossy) session label.

This module is **pure** (no DB, no async) and **capture-only**: nothing here drives
production state. The service layer reads the athlete's pre-log e1RM and persists the result
to ``dose_routing_shadow_log``; ``state_update`` is untouched until a later promotion PR.

Two spaces are kept strictly distinct (ADR-0054):

* **raw model space** — ``raw_X = Σ_i φ^X_i · D_i``, unbounded, model-native, stored for
  observability and the future tuning harness;
* **control space** — ``X_compat = k_X · raw_X`` in the existing 0–100 fatigue/tissue units
  the live deload / interference / safety thresholds already speak.

``k_X`` is a **versioned compatibility scale** (``dose_routing_compat_v1``), distribution-
matched to Model A over the sim corpus (see ``dose_routing_calibration``). It is a
compatibility bridge, **not** a validated physiology unit; raw φ·D never touches a threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.vectors import (
    AdaptationContribution,
    FatigueState,
    TissueState,
)
from app.engine.parameters import EngineParameters, default_parameters
from app.engine.phi_table import default_phi_for_row
from app.logic import strength_calibration as sc
from app.logic.domain_vocab import PHI_ADAPT_TO_CAPACITY
from app.logic.dose_engine_v0 import exercise_base_bundle, infer_movement_pattern
from app.schemas.workouts import ExerciseEntry, ExternalIntensity, WorkoutLog

# ---------------------------------------------------------------------------
# Versioned compatibility scales (control space). FROZEN constants; the
# calibration that derives them lives in ``dose_routing_calibration`` and a
# reproducibility test re-derives + asserts these values. See ADR-0054.
# ---------------------------------------------------------------------------
COMPAT_MODEL_VERSION = "dose_routing_compat_v1"
CALIBRATION_BASIS = "sim_scenario_distribution_match_v1"

# k_X = median(old_Model_A_delta / raw_phi_dot_D) over the sim corpus (see
# dose_routing_calibration.calibrate). A reproducibility test re-derives these and asserts
# they still hold. Frozen for dose_routing_compat_v1 on 2026-07-10.
K_FATIGUE_V1 = 24.4639
K_TISSUE_V1 = 4.5778
K_ADAPT_V1 = 1.1954
K_STRUCT_V1 = 50.126

# Routing-basis labels (the coverage/missingness ladder, never a λ blend).
BASIS_EXERCISE_PHI = "exercise_phi"
BASIS_UNRESOLVED_FALLBACK = "unresolved_exercise_fallback"
BASIS_SESSION_MODALITY_FALLBACK = "session_modality_fallback"

# φ_adapt keys that drive a structural (mTOR / strength) signal. Endurance-dominant work
# contributes ~0 (so concurrent endurance never feeds the Banister strength bump).
_STRUCT_FAMILY = ("strength", "max_strength", "hypertrophy", "power")

_UNRESOLVED_CONFIDENCE = 0.3
_RESOLVED_CONFIDENCE = 1.0


@dataclass
class ExerciseRoutingContribution:
    """One routing unit's per-axis routed dose plus its routing provenance."""

    exercise_id: int | None
    exercise_name: str | None
    routing_basis: str
    routing_confidence: float
    fallback_reason: str | None
    d_i: float
    intensity: float
    intensity_source: str
    raw_capacity: dict[str, float]
    raw_fatigue: dict[str, float]
    raw_tissue: dict[str, float]
    raw_struct: float


@dataclass
class ModelBRouting:
    """The full shadow routing result for one session (raw + control space + provenance)."""

    routing_basis: str
    model_version: str
    calibration_basis: str
    # raw model space (unbounded), per-axis totals
    raw_capacity: dict[str, float]
    raw_fatigue: dict[str, float]
    raw_tissue: dict[str, float]
    raw_struct: float
    # control space (0–100 compat, UNCLIPPED — observability keeps the full signal)
    capacity_compat: dict[str, float]
    fatigue_compat_0_100: dict[str, float]
    tissue_compat_0_100: dict[str, float]
    struct_compat: float
    # scales applied
    k: dict[str, float]
    # coverage / provenance
    n_units: int
    n_resolved_phi: int
    n_unresolved: int
    contributions: list[ExerciseRoutingContribution]


def _to_failure(reps: float | None, rpe: float | None, rir: float | None) -> bool:
    return (rir is not None and rir <= 0) or (rpe is not None and rpe >= 9.5)


def _struct_weight(phi_adapt: dict[str, Any]) -> float:
    """Structural (strength-family) share of an exercise's adaptation φ.

    Endurance-dominant work (aerobic φ ≥ strength-family φ) returns 0 — it carries ~no
    mTOR signal, so it must not feed the Banister strength bump (ADR-0037).
    """
    strength = sum(float(phi_adapt.get(k, 0.0)) for k in _STRUCT_FAMILY)
    aerobic = float(phi_adapt.get("aerobic", 0.0)) + float(phi_adapt.get("anaerobic", 0.0))
    if aerobic >= strength:
        return 0.0
    return strength


def _route_one(
    entry: ExerciseEntry,
    log: WorkoutLog,
    p: EngineParameters,
    *,
    e1rm_pre: float | None,
    basis: str,
    confidence: float,
    fallback_reason: str | None,
    intensity_override: sc.CalibrationResult | None = None,
) -> ExerciseRoutingContribution:
    """Compute ``D_i`` and route it through this unit's φ vectors."""
    base, phi_pack, _vol = exercise_base_bundle(entry, log, p)
    if intensity_override is not None:
        intensity = intensity_override
    else:
        intensity = sc.external_intensity_for_set(
            reps=entry.reps,
            load_kg=entry.load_kg,
            rpe=entry.avg_rpe,
            rir=entry.avg_rir,
            e1rm_pre=e1rm_pre,
            to_failure=_to_failure(entry.reps, entry.avg_rpe, entry.avg_rir),
        )
    d_i = base * (intensity.value ** p.dose_alpha)

    phi_adapt = phi_pack["phi_adapt"]
    phi_fatigue = phi_pack["phi_fatigue"]
    phi_tissue = phi_pack["phi_tissue"]

    raw_capacity: dict[str, float] = {}
    for phi_key, w in phi_adapt.items():
        cap_key = PHI_ADAPT_TO_CAPACITY.get(phi_key)
        if cap_key:
            raw_capacity[cap_key] = raw_capacity.get(cap_key, 0.0) + float(w) * d_i
    raw_fatigue = {k: float(v) * d_i for k, v in phi_fatigue.items() if k in FatigueState.KEYS}
    raw_tissue = {k: float(v) * d_i for k, v in phi_tissue.items() if k in TissueState.KEYS}
    raw_struct = d_i * _struct_weight(phi_adapt)

    return ExerciseRoutingContribution(
        exercise_id=entry.exercise_id,
        exercise_name=entry.exercise_name,
        routing_basis=basis,
        routing_confidence=confidence,
        fallback_reason=fallback_reason,
        d_i=d_i,
        intensity=intensity.value,
        intensity_source=intensity.source,
        raw_capacity=raw_capacity,
        raw_fatigue=raw_fatigue,
        raw_tissue=raw_tissue,
        raw_struct=raw_struct,
    )


def _session_pseudo_entry(log: WorkoutLog) -> ExerciseEntry:
    """Reconstruct the whole session as one φ-carrying pseudo-exercise (fallback tier).

    φ is the conservative session-modality default; volume is rebuilt from the session
    fields so it flows through the same per-exercise base as resolved work (no divergent
    base formula).
    """
    sets = log.estimated_sets or max(3.0, log.duration_minutes / 12.0)
    vol = log.total_volume_load or 0.0
    reps = 5.0
    load = (vol / (sets * reps)) if vol > 0 else None
    phi = default_phi_for_row(
        log.modality,
        infer_movement_pattern(log),
        skill_demand=0.55 if log.modality in ("Power", "Mixed") else 0.45,
        impact_level=0.75 if log.modality == "Running" else 0.55,
    )
    return ExerciseEntry(
        sets=sets,
        reps=reps,
        load_kg=load,
        duration_seconds=log.duration_minutes * 60.0,
        distance_meters=log.distance_meters,
        avg_rpe=log.session_rpe,
        avg_rir=log.avg_rir,
        phi_adapt=phi["phi_adapt"],
        phi_fatigue=phi["phi_fatigue"],
        phi_tissue=phi["phi_tissue"],
        energy_mix=phi["energy_mix"],
        modality=log.modality,
        movement_pattern=infer_movement_pattern(log),
    )


def build_routing(
    log: WorkoutLog,
    *,
    e1rm_by_key: dict[str, float] | None = None,
    external_intensity: ExternalIntensity | None = None,
    params: EngineParameters | None = None,
) -> ModelBRouting:
    """Route a session's dose per-exercise (raw φ·D) and cross into 0–100 control space.

    ``e1rm_by_key`` maps an exercise identity (``"id:<n>"`` or ``"name:<name>"``) to its
    pre-log e1RM denominator (read by the service from uncorrupted authority, ADR-0055).
    ``external_intensity`` (Model A session scalar) supplies ``I`` for the session-modality
    fallback tier. Coverage is a ladder, never a λ blend (see :data:`BASIS_EXERCISE_PHI`).
    """
    p = params or default_parameters()
    e1rm_by_key = e1rm_by_key or {}

    resolved = [e for e in log.exercises if e.phi_adapt]
    contributions: list[ExerciseRoutingContribution] = []

    if resolved:
        session_basis = BASIS_EXERCISE_PHI
        for entry in log.exercises:
            key = (
                f"id:{entry.exercise_id}"
                if entry.exercise_id is not None
                else f"name:{entry.exercise_name}"
            )
            if entry.phi_adapt:
                contributions.append(
                    _route_one(
                        entry, log, p,
                        e1rm_pre=e1rm_by_key.get(key),
                        basis=BASIS_EXERCISE_PHI,
                        confidence=_RESOLVED_CONFIDENCE,
                        fallback_reason=None,
                    )
                )
            else:
                # Unresolved within an exercise-routed session: conservative substitute
                # φ (from the entry's own metadata, else session modality) — dose is
                # NEVER erased, just low-confidence and labeled.
                sub = entry.model_copy(deep=True)
                if not sub.phi_adapt:
                    phi = default_phi_for_row(
                        sub.modality or log.modality,
                        sub.movement_pattern or infer_movement_pattern(log),
                        float(sub.skill_demand or 0.45),
                        float(sub.impact_level or 0.55),
                    )
                    sub.phi_adapt = phi["phi_adapt"]
                    sub.phi_fatigue = phi["phi_fatigue"]
                    sub.phi_tissue = phi["phi_tissue"]
                    sub.energy_mix = phi["energy_mix"]
                contributions.append(
                    _route_one(
                        sub, log, p,
                        e1rm_pre=e1rm_by_key.get(key),
                        basis=BASIS_UNRESOLVED_FALLBACK,
                        confidence=_UNRESOLVED_CONFIDENCE,
                        fallback_reason="missing_exercise_phi",
                    )
                )
    else:
        # No dose-bearing exercise has resolved φ → one conservative session-modality unit.
        session_basis = BASIS_SESSION_MODALITY_FALLBACK
        pseudo = _session_pseudo_entry(log)
        override = (
            sc.CalibrationResult(
                value=external_intensity.value,
                source=external_intensity.source,
                confidence=external_intensity.confidence,
            )
            if external_intensity is not None
            else None
        )
        contributions.append(
            _route_one(
                pseudo, log, p,
                e1rm_pre=None,
                basis=BASIS_SESSION_MODALITY_FALLBACK,
                confidence=_UNRESOLVED_CONFIDENCE,
                fallback_reason="no_resolved_exercise_phi",
                intensity_override=override,
            )
        )

    raw_capacity: dict[str, float] = dict.fromkeys(AdaptationContribution.KEYS, 0.0)
    raw_fatigue: dict[str, float] = dict.fromkeys(FatigueState.KEYS, 0.0)
    raw_tissue: dict[str, float] = dict.fromkeys(TissueState.KEYS, 0.0)
    raw_struct = 0.0
    for c in contributions:
        for k, v in c.raw_capacity.items():
            raw_capacity[k] = raw_capacity.get(k, 0.0) + v
        for k, v in c.raw_fatigue.items():
            raw_fatigue[k] += v
        for k, v in c.raw_tissue.items():
            raw_tissue[k] += v
        raw_struct += c.raw_struct

    return ModelBRouting(
        routing_basis=session_basis,
        model_version=COMPAT_MODEL_VERSION,
        calibration_basis=CALIBRATION_BASIS,
        raw_capacity=raw_capacity,
        raw_fatigue=raw_fatigue,
        raw_tissue=raw_tissue,
        raw_struct=raw_struct,
        capacity_compat={k: v * K_ADAPT_V1 for k, v in raw_capacity.items()},
        fatigue_compat_0_100={k: v * K_FATIGUE_V1 for k, v in raw_fatigue.items()},
        tissue_compat_0_100={k: v * K_TISSUE_V1 for k, v in raw_tissue.items()},
        struct_compat=raw_struct * K_STRUCT_V1,
        k={
            "fatigue": K_FATIGUE_V1,
            "tissue": K_TISSUE_V1,
            "adapt": K_ADAPT_V1,
            "struct": K_STRUCT_V1,
        },
        n_units=len(contributions),
        n_resolved_phi=sum(1 for c in contributions if c.routing_basis == BASIS_EXERCISE_PHI),
        n_unresolved=sum(
            1 for c in contributions if c.routing_basis == BASIS_UNRESOLVED_FALLBACK
        ),
        contributions=contributions,
    )
