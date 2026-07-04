"""
ORM ↔ UnifiedStateVector: engine_state JSONB and legacy scalar columns stay aligned.

Versioning / migration strategy
-------------------------------
Each persisted ``engine_state`` payload carries a ``version`` key
(``ENGINE_STATE_SCHEMA_VERSION``). When the decomposed-vector schema changes,
bump that constant and add an upgrade branch in ``_migrate_engine_state`` keyed
on the stored version. Historical rows then migrate **lazily on read** — the
version lives inside the JSONB column, so evolving the schema needs no Alembic
migration. Payloads without the x/f/t vectors fall back to the legacy scalar
columns.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.schemas.engine_vectors import (
    CapacityConfidence,
    CapacityState,
    FatigueState,
    TissueState,
)
from app.schemas.state import UnifiedStateVector

# Schema version of the engine_state JSONB payload. Bump when the x/f/t vector
# shapes change, and add a matching upgrade branch in _migrate_engine_state.
#   v2: added "c" — per-axis capacity confidence (ADR-0036).
ENGINE_STATE_SCHEMA_VERSION = 2


def default_engine_state_dict() -> dict[str, Any]:
    return {
        "version": ENGINE_STATE_SCHEMA_VERSION,
        "x": CapacityState().model_dump(),
        "f": FatigueState().model_dump(),
        "t": TissueState().model_dump(),
        "c": CapacityConfidence().model_dump(),
    }


def _migrate_engine_state(eng: dict[str, Any]) -> dict[str, Any] | None:
    """
    Upgrade a persisted engine_state payload to the current schema version.

    Historical rows migrate lazily on read. Unversioned payloads (written before
    this stamp existed) are treated as v1. When the vector schema changes, bump
    ``ENGINE_STATE_SCHEMA_VERSION`` and add an upgrade branch here, e.g.::

        if version < 2:
            eng = _upgrade_v1_to_v2(eng)

    Returns None when the payload lacks the x/f/t vectors, so the caller falls
    back to the legacy scalar columns.
    """
    if not ("x" in eng and "f" in eng and "t" in eng):
        return None
    migrated = dict(eng)
    # v1 → v2: seed a weak-prior capacity confidence when absent (ADR-0036).
    if "c" not in migrated:
        migrated["c"] = CapacityConfidence().model_dump()
    # Ensure the (possibly legacy/unversioned) payload carries a current stamp.
    migrated["version"] = ENGINE_STATE_SCHEMA_VERSION
    return migrated


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


# max_strength axis calibration. The legacy scalar ``c_nm_force`` is squat_kg * 10,
# and the axis is anchored to the squat e1RM benchmark (`pl_e1rm_squat`: floor 40kg,
# cap 250kg — see app/scripts/seed_benchmarks.py). So
#   max_strength = clamp01((c_nm_force/10 - 40) / (250 - 40)) * 100
#              = clamp(0, 100, (c_nm_force - 400) / 21).
# This keeps the experience-prior seed AND the benchmark anchor on ONE scale: a
# logged squat no longer yanks a freshly-seeded athlete, and intermediate/advanced
# athletes get real headroom (the old /10 pegged any >=100kg squatter at the ceiling).
# Power derives from the same force scalar as a gentler-sloped prior (its own
# power/clean benchmarks refine it), sharing the 40kg floor.
STRENGTH_FLOOR_CNM = 400.0   # 40kg squat * 10 (benchmark floor)
STRENGTH_SLOPE_CNM = 21.0    # (250-40)kg * 10 / 100 axis points
POWER_SLOPE_CNM = 28.0       # gentler than strength → power sits below max_strength


def capacity_from_legacy(
    c_met_aerobic: float,
    c_nm_force: float,
    c_struct: float,
    b_met_anaerobic: float,
) -> CapacityState:
    return CapacityState(
        aerobic=c_met_aerobic,
        glycolytic=min(100.0, max(0.0, b_met_anaerobic / 300.0)),
        max_strength=min(100.0, max(0.0, (c_nm_force - STRENGTH_FLOOR_CNM) / STRENGTH_SLOPE_CNM)),
        hypertrophy=min(100.0, max(0.0, c_struct * 0.5)),
        power=min(100.0, max(0.0, (c_nm_force - STRENGTH_FLOOR_CNM) / POWER_SLOPE_CNM)),
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
        # Inverse of capacity_from_legacy's max_strength affine (keeps the mirror
        # round-tripping): c_nm_force = max_strength * 21 + 400.
        "c_nm_force": x.max_strength * STRENGTH_SLOPE_CNM + STRENGTH_FLOOR_CNM,
        "c_struct": max(1.0, x.hypertrophy * 1.2 + x.work_capacity * 0.4),
        "b_met_anaerobic": max(0.0, x.glycolytic * 300.0),
        "f_met_systemic": f.metabolic,
        "f_nm_peripheral": f.muscular,
        "f_nm_central": f.cns,
        "f_struct_damage": f_struct_combined,
    }


def build_unified_state_vector(
    *,
    timestamp: datetime,
    x: CapacityState,
    f: FatigueState,
    t: TissueState,
    **kwargs: Any,
) -> UnifiedStateVector:
    """Construct UnifiedStateVector with legacy scalars derived from x/f/t."""
    leg = sync_legacy_from_vectors(x, f, t)
    return UnifiedStateVector(
        timestamp=timestamp,
        capacity_x=x,
        fatigue_f=f,
        tissue_t=t,
        c_met_aerobic=leg["c_met_aerobic"],
        c_nm_force=leg["c_nm_force"],
        c_struct=leg["c_struct"],
        b_met_anaerobic=leg["b_met_anaerobic"],
        f_met_systemic=leg["f_met_systemic"],
        f_nm_peripheral=leg["f_nm_peripheral"],
        f_nm_central=leg["f_nm_central"],
        f_struct_damage=leg["f_struct_damage"],
        **kwargs,
    )


def unified_from_athlete_row(row: Any) -> UnifiedStateVector:
    """Build UnifiedStateVector from SQLAlchemy AthleteState row."""
    raw_eng = _parse_engine_state(getattr(row, "engine_state", None))
    eng = _migrate_engine_state(raw_eng) if raw_eng is not None else None

    if eng is not None:
        x = CapacityState.model_validate(eng["x"])
        f = FatigueState.model_validate(eng["f"])
        t = TissueState.model_validate(eng["t"])
        c = CapacityConfidence.model_validate(eng.get("c") or CapacityConfidence().model_dump())
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
        c = CapacityConfidence()  # legacy scalar rows → weak prior

    legacy = sync_legacy_from_vectors(x, f, t)

    ts: datetime = row.timestamp
    return UnifiedStateVector(
        timestamp=ts,
        capacity_x=x,
        fatigue_f=f,
        tissue_t=t,
        capacity_confidence=c,
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
        "version": ENGINE_STATE_SCHEMA_VERSION,
        "x": s.capacity_x.model_dump(),
        "f": s.fatigue_f.model_dump(),
        "t": s.tissue_t.model_dump(),
        "c": s.capacity_confidence.model_dump(),
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
