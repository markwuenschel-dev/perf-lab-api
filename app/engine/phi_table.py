"""
Default φ_adapt / φ_fatigue / φ_tissue weights by modality and movement pattern.

Used when exercise rows omit explicit JSON phi vectors (seed backfill).
"""

from __future__ import annotations

from typing import Any


def _merge(base: dict[str, float], extra: dict[str, float]) -> dict[str, float]:
    out = dict(base)
    for k, v in extra.items():
        out[k] = out.get(k, 0.0) + v
    return out


def default_phi_for_row(
    modality: str,
    movement_pattern: str,
    skill_demand: float,
    impact_level: float,
) -> dict[str, Any]:
    """Returns dicts suitable for Exercise.phi_* JSON columns."""
    adapt = {
        "strength": 0.15,
        "hypertrophy": 0.15,
        "power": 0.1,
        "aerobic": 0.1,
        "anaerobic": 0.1,
        "skill": max(0.05, skill_demand * 0.25),
        "mobility": 0.05,
    }
    fatigue = {
        "cns": 0.1 + skill_demand * 0.35,
        "muscular": 0.25,
        "metabolic": 0.15,
        "structural": impact_level * 0.25,
        "tendon": impact_level * 0.2,
        "grip": 0.05,
    }
    tissue = {k: 0.05 for k in ("shoulder", "elbow", "wrist", "lumbar", "hip", "knee", "ankle", "finger")}

    mp = movement_pattern.lower()
    if "squat" in mp or mp == "single_leg":
        tissue = _merge(tissue, {"hip": 0.2, "knee": 0.2, "lumbar": 0.15})
        adapt = _merge(adapt, {"strength": 0.2, "hypertrophy": 0.15})
        fatigue = _merge(fatigue, {"muscular": 0.15, "structural": 0.1})
    if "hinge" in mp or "deadlift" in mp:
        tissue = _merge(tissue, {"lumbar": 0.25, "hip": 0.15})
        fatigue = _merge(fatigue, {"grip": 0.2, "structural": 0.1})
        adapt = _merge(adapt, {"strength": 0.25})
    if "push" in mp:
        tissue = _merge(tissue, {"shoulder": 0.2, "elbow": 0.1})
    if "pull" in mp:
        tissue = _merge(tissue, {"shoulder": 0.15, "elbow": 0.1, "finger": 0.1})
        fatigue = _merge(fatigue, {"grip": 0.15})
    if "run" in mp or modality == "Running":
        adapt = _merge(adapt, {"aerobic": 0.45, "anaerobic": 0.2})
        fatigue = _merge(fatigue, {"metabolic": 0.35, "structural": 0.25, "tendon": 0.2})
        tissue = _merge(tissue, {"ankle": 0.2, "knee": 0.2, "hip": 0.1})
    if modality in ("Power",):
        adapt = _merge(adapt, {"power": 0.35, "skill": 0.15})
        fatigue = _merge(fatigue, {"cns": 0.25, "metabolic": 0.1})
    if modality in ("Hypertrophy",):
        adapt = _merge(adapt, {"hypertrophy": 0.35})
        fatigue = _merge(fatigue, {"muscular": 0.2})
    if modality in ("Calisthenics",):
        adapt = _merge(adapt, {"skill": 0.3, "strength": 0.15})
        fatigue = _merge(fatigue, {"grip": 0.15, "cns": 0.15})
    if modality in ("Conditioning", "Mixed"):
        adapt = _merge(adapt, {"aerobic": 0.25, "anaerobic": 0.35})
        fatigue = _merge(fatigue, {"metabolic": 0.3, "muscular": 0.15})

    valid_tissue = (
        "shoulder",
        "elbow",
        "wrist",
        "lumbar",
        "hip",
        "knee",
        "ankle",
        "finger",
    )
    tissue = {k: v for k, v in tissue.items() if k in valid_tissue}

    return {
        "phi_adapt": adapt,
        "phi_fatigue": fatigue,
        "phi_tissue": tissue,
        "energy_mix": default_energy_mix(modality),
    }


def default_energy_mix(modality: str) -> dict[str, float]:
    m = modality.lower()
    if "run" in m:
        return {"aerobic": 0.7, "glycolytic": 0.25, "alactic": 0.05}
    if modality in ("Power",):
        return {"aerobic": 0.1, "glycolytic": 0.35, "alactic": 0.55}
    if modality in ("Hypertrophy", "Strength"):
        return {"aerobic": 0.15, "glycolytic": 0.55, "alactic": 0.3}
    if modality in ("Running", "Conditioning"):
        return {"aerobic": 0.55, "glycolytic": 0.35, "alactic": 0.1}
    return {"aerobic": 0.33, "glycolytic": 0.33, "alactic": 0.34}
