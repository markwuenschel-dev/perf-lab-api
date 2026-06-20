"""
ORM ↔ UnifiedStateVector: engine_state JSONB and legacy scalar columns stay aligned.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def default_engine_state_dict() -> dict[str, Any]:
    return {
        "x": CapacityState().model_dump(),
        "f": FatigueState().model_dump(),
        "t": TissueState().model_dump(),
    }


def _parse_engine_state(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def fatigue_from_legacy(
    f_met_systemic: float,
    f_nm_peripheral: float,
    f_nm_central: float,
    f_struct_damage: float,
) -> FatigueState:
    """Bootstrap F from legacy scalars when engine_state is absent."""
    return FatigueState(
        cns=min(100.0, f_nm_central),
        muscular=min(100.0, f_nm_peripheral),
        metabolic=min(100.0, f_met_systemic),
        structural=min(100.0, f_struct_damage * 0.55),
        tendon=min(100.0, f_struct_damage * 0.35),
        grip=min(100.0, f_struct_damage * 0.25),
    )


def tissue_from_legacy(f_struct_damage: float) -> TissueState:
    """Rough uniform tissue stress from legacy structural fatigue."""
    v = min(100.0, f_struct_damage * 0.35)
    return TissueState(
        shoulder=v,
        elbow=v * 0.7,
        wrist=v * 0.6,
        lumbar=v * 1.1,
        hip=v * 0.9,
        knee=v * 0.9,
        ankle=v * 0.7,
        finger=v * 0.5,
    )


def capacity_from_legacy(
    c_met_aerobic: float,
    c_nm_force: float,
    c_struct: float,
    b_met_anaerobic: float,
) -> CapacityState:
    return CapacityState(
        aerobic=c_met_aerobic,
        glycolytic=min(100.0, max(0.0, b_met_anaerobic / 300.0)),
        max_strength=min(100.0, max(0.0, c_nm_force / 10.0)),
        hypertrophy=min(100.0, max(0.0, c_struct * 0.5)),
        power=min(100.0, max(0.0, c_nm_force / 15.0)),
        skill=50.0,
        mobility=50.0,
        work_capacity=min(100.0, max(0.0, c_struct * 0.35)),
    )


def sync_legacy_from_vectors(
    x: CapacityState,
    f: FatigueState,
    t: TissueState,
) -> dict[str, float]:
    """Derive legacy AthleteState columns for API/back-compat."""
    tissue_avg = sum(t.model_dump().values()) / max(1, len(TissueState.KEYS))
    f_struct_combined = min(
        100.0,
        f.structural + f.tendon + 0.15 * f.grip + 0.1 * tissue_avg,
    )
    return {
        "c_met_aerobic": x.aerobic,
        "c_nm_force": x.max_strength * 10.0,
        "c_struct": max(1.0, x.hypertrophy * 1.2 + x.work_capacity * 0.4),
        "b_met_anaerobic": max(0.0, x.glycolytic * 300.0),
        "f_met_systemic": f.metabolic,
        "f_nm_peripheral": f.muscular,
        "f_nm_central": f.cns,
        "f_struct_damage": f_struct_combined,
    }


def unified_from_athlete_row(row: Any) -> UnifiedStateVector:
    """Build UnifiedStateVector from SQLAlchemy AthleteState row."""
    eng = _parse_engine_state(getattr(row, "engine_state", None))

    if eng and "x" in eng and "f" in eng and "t" in eng:
        x = CapacityState.model_validate(eng["x"])
        f = FatigueState.model_validate(eng["f"])
        t = TissueState.model_validate(eng["t"])
    else:
        x = capacity_from_legacy(
            row.c_met_aerobic,
            row.c_nm_force,
            row.c_struct,
            row.b_met_anaerobic,
        )
        f = fatigue_from_legacy(
            row.f_met_systemic,
            row.f_nm_peripheral,
            row.f_nm_central,
            row.f_struct_damage,
        )
        t = tissue_from_legacy(row.f_struct_damage)

    legacy = sync_legacy_from_vectors(x, f, t)

    ts: datetime = row.timestamp
    return UnifiedStateVector(
        timestamp=ts,
        capacity_x=x,
        fatigue_f=f,
        tissue_t=t,
        c_met_aerobic=legacy["c_met_aerobic"],
        c_nm_force=legacy["c_nm_force"],
        c_struct=legacy["c_struct"],
        b_met_anaerobic=legacy["b_met_anaerobic"],
        f_met_systemic=legacy["f_met_systemic"],
        f_nm_peripheral=legacy["f_nm_peripheral"],
        f_nm_central=legacy["f_nm_central"],
        f_struct_damage=legacy["f_struct_damage"],
        s_struct_signal=float(getattr(row, "s_struct_signal", 0.0) or 0.0),
        habit_strength=float(getattr(row, "habit_strength", 0.0) or 0.0),
        skill_state=dict(getattr(row, "skill_state", None) or {}),
    )


def athlete_state_kwargs_from_unified(s: UnifiedStateVector) -> dict[str, Any]:
    """Keyword args for inserting AthleteState ORM row."""
    eng = {
        "x": s.capacity_x.model_dump(),
        "f": s.fatigue_f.model_dump(),
        "t": s.tissue_t.model_dump(),
    }
    return {
        "timestamp": s.timestamp,
        "c_met_aerobic": s.c_met_aerobic,
        "c_nm_force": s.c_nm_force,
        "c_struct": s.c_struct,
        "b_met_anaerobic": s.b_met_anaerobic,
        "f_met_systemic": s.f_met_systemic,
        "f_nm_peripheral": s.f_nm_peripheral,
        "f_nm_central": s.f_nm_central,
        "f_struct_damage": s.f_struct_damage,
        "s_struct_signal": s.s_struct_signal,
        "habit_strength": s.habit_strength,
        "skill_state": s.skill_state,
        "engine_state": eng,
    }
