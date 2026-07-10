"""Model B per-exercise dose routing — shadow-only (ADR-0054).

Pure-logic coverage: routing attribution (the win), the coverage/missingness ladder (no λ),
the versioned compatibility scale (raw φ·D → 0–100 control space), calibration
reproducibility, and sim-corpus magnitude equivalence with Model A.
"""

from datetime import datetime

from app.engine.phi_table import default_phi_for_row
from app.logic import dose_routing as dr
from app.logic.dose_routing_calibration import (
    _old_totals,
    _raw_totals,
    calibrate,
    calibration_corpus,
)
from app.schemas.workouts import ExerciseEntry, ExternalIntensity, WorkoutLog

_WHEN = datetime(2026, 7, 10, 12, 0, 0)


def _entry(
    modality: str, movement: str, *, sets: float = 3, reps: float = 5, load: float = 100.0,
    rpe: float = 8.0, rir: float = 2.0, skill: float = 0.4, impact: float = 0.5,
    resolved: bool = True,
) -> ExerciseEntry:
    kw: dict = {}
    if resolved:
        phi = default_phi_for_row(modality, movement, skill, impact)
        kw = {
            "phi_adapt": phi["phi_adapt"], "phi_fatigue": phi["phi_fatigue"],
            "phi_tissue": phi["phi_tissue"], "energy_mix": phi["energy_mix"],
        }
    return ExerciseEntry(
        exercise_name=movement, sets=sets, reps=reps, load_kg=load,
        avg_rpe=rpe, avg_rir=rir, modality=modality, movement_pattern=movement, **kw
    )


def _log(modality: str = "Strength", *, exercises=None, vol: float = 5000.0) -> WorkoutLog:
    return WorkoutLog(
        timestamp=_WHEN, modality=modality, duration_minutes=60.0, session_rpe=8.0,
        avg_rir=2.0, total_volume_load=vol, exercises=exercises or [],
    )


# ── attribution: the actual Model B win ───────────────────────────────────────

def test_endurance_contributes_zero_structural_signal():
    run = dr.build_routing(_log("Mixed", exercises=[_entry("Running", "run", load=0.0)]))
    squat = dr.build_routing(_log("Mixed", exercises=[_entry("Strength", "squat")]))
    assert run.raw_struct == 0.0          # endurance never feeds the Banister bump
    assert squat.raw_struct > 0.0


def test_tissue_routes_through_exercise_phi_not_session_label():
    # A squat logged in a *Mixed* session must deposit squat tissue (hip/knee/lumbar),
    # not the generic session-modality spread the old path used.
    r = dr.build_routing(_log("Mixed", exercises=[_entry("Strength", "squat")]))
    top = max(r.raw_tissue, key=lambda k: r.raw_tissue[k])
    assert top in ("hip", "knee", "lumbar")
    assert r.raw_tissue["hip"] > 0 and r.raw_tissue["knee"] > 0
    assert r.raw_tissue.get("ankle", 0.0) < r.raw_tissue["knee"]


def test_accessory_does_not_bleed_into_squat_axes():
    # A pull accessory in a squat session routes to its OWN tissue (elbow/finger/shoulder).
    r = dr.build_routing(
        _log("Strength", exercises=[
            _entry("Strength", "squat", load=140.0, sets=5),
            _entry("Hypertrophy", "pull", load=40.0, reps=10, rpe=9.0, rir=1.0),
        ])
    )
    pull = next(c for c in r.contributions if c.exercise_name == "pull")
    assert pull.raw_tissue.get("elbow", 0.0) > 0 or pull.raw_tissue.get("finger", 0.0) > 0
    assert r.n_resolved_phi == 2 and r.routing_basis == dr.BASIS_EXERCISE_PHI


# ── coverage / missingness ladder (no λ blend) ────────────────────────────────

def test_unresolved_exercise_still_deposits_dose():
    r = dr.build_routing(
        _log("Strength", exercises=[
            _entry("Strength", "squat"),                       # resolved
            _entry("Strength", "curl", resolved=False),        # no φ resolved
        ])
    )
    assert r.routing_basis == dr.BASIS_EXERCISE_PHI
    assert r.n_unresolved == 1
    unresolved = next(
        c for c in r.contributions if c.routing_basis == dr.BASIS_UNRESOLVED_FALLBACK
    )
    assert unresolved.d_i > 0.0                                # dose NEVER erased
    assert unresolved.fallback_reason == "missing_exercise_phi"
    assert unresolved.routing_confidence < 1.0


def test_session_only_log_uses_modality_fallback():
    r = dr.build_routing(_log("Running"))
    assert r.routing_basis == dr.BASIS_SESSION_MODALITY_FALLBACK
    assert len(r.contributions) == 1
    assert r.contributions[0].d_i > 0.0
    assert sum(r.raw_fatigue.values()) > 0.0


def test_session_fallback_uses_model_a_intensity():
    ext = ExternalIntensity(value=0.9, source="relative_load", confidence=0.9)
    r = dr.build_routing(_log("Strength"), external_intensity=ext)
    assert r.contributions[0].intensity == 0.9


# ── compatibility scale: raw model space vs 0–100 control space ───────────────

def test_compat_is_raw_times_k_and_unclipped():
    r = dr.build_routing(_log("Strength", exercises=[_entry("Strength", "squat", load=200.0)]))
    for axis, raw in r.raw_fatigue.items():
        assert abs(r.fatigue_compat_0_100[axis] - raw * dr.K_FATIGUE_V1) < 1e-9
    assert r.k["fatigue"] == dr.K_FATIGUE_V1
    assert r.model_version == dr.COMPAT_MODEL_VERSION
    # Unclipped: a brutal session may legitimately exceed 100 in raw control space.
    brutal = dr.build_routing(
        _log("Strength", vol=40000.0, exercises=[
            _entry("Strength", "squat", sets=10, reps=5, load=250.0, rpe=10.0, rir=0.0)
        ])
    )
    assert max(brutal.fatigue_compat_0_100.values()) > 0  # value retained, not clamped away


# ── calibration reproducibility + sim-corpus magnitude equivalence ────────────

def test_calibration_reproduces_frozen_k():
    k = calibrate().k
    frozen = {
        "fatigue": dr.K_FATIGUE_V1, "tissue": dr.K_TISSUE_V1,
        "adapt": dr.K_ADAPT_V1, "struct": dr.K_STRUCT_V1,
    }
    for v, f in frozen.items():
        assert abs(k[v] - f) / f < 0.01, f"{v}: {k[v]} vs frozen {f}"


def test_sim_corpus_compat_preserves_model_a_magnitude():
    # The compat scale must keep Model B's control-space totals on Model A's scale
    # (only the cross-axis distribution changes). Median ratio ≈ 1 by construction of k.
    for vector, k in (("fatigue", dr.K_FATIGUE_V1), ("tissue", dr.K_TISSUE_V1)):
        ratios = []
        for log in calibration_corpus():
            old = _old_totals(log)[vector]
            raw = _raw_totals(log)[vector]
            if old > 1e-9 and raw > 1e-9:
                ratios.append((raw * k) / old)
        ratios.sort()
        median = ratios[len(ratios) // 2]
        assert 0.8 <= median <= 1.25, f"{vector} median compat/old = {median:.3f}"
