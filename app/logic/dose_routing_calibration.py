"""Compatibility-scale calibration for Model B dose routing (ADR-0054).

Derives the versioned ``k_X`` scalars that bridge raw ``Σ φ·D`` (model space) into the
existing 0–100 fatigue/tissue **control space** — chosen by **distribution-matching**
Model B's raw routed dose to Model A's impulse over a representative corpus, NOT by vibes:

    k_X = median( old_Model_A_delta_X / raw_ModelB_dose_X )   over eligible sessions.

There is no first-party historical session corpus (prod has ~0 logged sessions), so the
basis is the deterministic ``simulate.py`` scenario surface — hence
``CALIBRATION_BASIS = "sim_scenario_distribution_match_v1"``. The frozen ``K_*_V1``
constants in :mod:`app.logic.dose_routing` are the output of :func:`calibrate`; a
reproducibility test re-runs this and asserts they still match. This is a **compatibility
bridge, not a validated physiology unit** — see the ADR.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime

from app.domain.vectors import FatigueState, TissueState
from app.engine import simulate
from app.logic import dose_routing as dr
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.logic.state_update_v0 import (
    fatigue_impulse_from_dose,
    tissue_impulse_from_dose,
)
from app.schemas.workouts import ExerciseEntry, WorkoutLog

_WHEN = datetime(2026, 1, 1, 12, 0, 0)


def _phi_entry(
    modality: str, movement: str, *, sets: float, reps: float, load: float,
    rpe: float, rir: float | None, skill: float = 0.4, impact: float = 0.5,
) -> ExerciseEntry:
    from app.engine.phi_table import default_phi_for_row

    phi = default_phi_for_row(modality, movement, skill, impact)
    return ExerciseEntry(
        exercise_name=f"{movement}_lift",
        sets=sets, reps=reps, load_kg=load, avg_rpe=rpe, avg_rir=rir,
        phi_adapt=phi["phi_adapt"], phi_fatigue=phi["phi_fatigue"],
        phi_tissue=phi["phi_tissue"], energy_mix=phi["energy_mix"],
        modality=modality, movement_pattern=movement,
        skill_demand=skill, impact_level=impact,
    )


def calibration_corpus() -> list[WorkoutLog]:
    """A representative spread of sessions across modality × intensity × recovery.

    Covers both routing tiers: session-level logs (→ ``session_modality_fallback``) and
    a set of exercise-resolved logs (→ ``exercise_phi``), so a single ``k`` per vector is
    validated against both regimes rather than one toy session.
    """
    corpus: list[WorkoutLog] = []
    # Session-level sim scenarios across bands (fallback tier).
    for modality in ("Strength", "Hypertrophy", "Power", "Running", "Mixed"):
        for intensity in ("easy", "balanced", "hard"):
            for recovery in ("high", "standard", "minimal"):
                for scale in (0.85, 1.0, 1.2):
                    corpus.append(
                        simulate.session_log_from_intent(
                            _WHEN, modality, scale=scale,
                            intensity=intensity, recovery=recovery,
                        )
                    )
    # Exercise-resolved logs (exercise_phi tier): single- and mixed-modality sessions.
    corpus.append(
        WorkoutLog(
            timestamp=_WHEN, modality="Strength", duration_minutes=60.0, session_rpe=8.0,
            avg_rir=2.0, total_volume_load=6000.0,
            sleep_quality=7.0, life_stress_inverse=7.0, novelty=1.0,
            exercises=[
                _phi_entry("Strength", "squat", sets=5, reps=5, load=140.0, rpe=8.0, rir=2.0),
                _phi_entry("Strength", "hinge", sets=3, reps=5, load=180.0, rpe=8.0, rir=2.0),
            ],
        )
    )
    corpus.append(
        WorkoutLog(
            timestamp=_WHEN, modality="Mixed", duration_minutes=55.0, session_rpe=8.0,
            avg_rir=2.0, total_volume_load=3500.0,
            sleep_quality=7.0, life_stress_inverse=7.0, novelty=1.0,
            exercises=[
                _phi_entry("Strength", "squat", sets=4, reps=5, load=120.0, rpe=8.0, rir=2.0),
                _phi_entry("Hypertrophy", "pull", sets=3, reps=10, load=40.0, rpe=9.0, rir=1.0),
            ],
        )
    )
    return corpus


def _old_totals(log: WorkoutLog) -> dict[str, float]:
    dose = calculate_stress_dose(log)
    fat = fatigue_impulse_from_dose(dose)
    tis = tissue_impulse_from_dose(dose, log)
    ac = dose.adaptation_contribution
    return {
        "fatigue": sum(float(getattr(fat, k)) for k in FatigueState.KEYS),
        "tissue": sum(float(tis.get(k, 0.0)) for k in TissueState.KEYS),
        "adapt": sum(float(getattr(ac, k)) for k in ac.KEYS),
        "struct": float(dose.d_struct_signal),
    }


def _raw_totals(log: WorkoutLog) -> dict[str, float]:
    r = dr.build_routing(log)
    return {
        "fatigue": sum(r.raw_fatigue.values()),
        "tissue": sum(r.raw_tissue.values()),
        "adapt": sum(r.raw_capacity.values()),
        "struct": r.raw_struct,
    }


@dataclass
class CalibrationReport:
    k: dict[str, float]
    n_eligible: dict[str, int]
    percentiles: dict[str, dict[str, float]]  # vector -> {p50,p75,p90,p95} of old delta


def calibrate(corpus: list[WorkoutLog] | None = None) -> CalibrationReport:
    """Derive ``k_X = median(old_delta / raw_dose)`` per vector over the corpus."""
    logs = corpus if corpus is not None else calibration_corpus()
    ratios: dict[str, list[float]] = {v: [] for v in ("fatigue", "tissue", "adapt", "struct")}
    old_deltas: dict[str, list[float]] = {v: [] for v in ratios}

    for log in logs:
        old = _old_totals(log)
        raw = _raw_totals(log)
        for v in ratios:
            if raw[v] > 1e-9 and old[v] > 1e-9:
                ratios[v].append(old[v] / raw[v])
                old_deltas[v].append(old[v])

    def _pcts(xs: list[float]) -> dict[str, float]:
        if not xs:
            return {"p50": 0.0, "p75": 0.0, "p90": 0.0, "p95": 0.0}
        s = sorted(xs)
        def q(p: float) -> float:
            return s[min(len(s) - 1, int(p * len(s)))]
        return {"p50": q(0.50), "p75": q(0.75), "p90": q(0.90), "p95": q(0.95)}

    return CalibrationReport(
        k={v: (statistics.median(r) if r else 1.0) for v, r in ratios.items()},
        n_eligible={v: len(r) for v, r in ratios.items()},
        percentiles={v: _pcts(old_deltas[v]) for v in ratios},
    )
