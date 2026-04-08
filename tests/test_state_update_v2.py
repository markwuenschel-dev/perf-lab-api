"""
Tests for state_update v2:
- Explicit adaptation gains by axis
- High fatigue suppresses adaptation
- CNS fatigue suppresses skill gains
- Benchmark timestamp chronology
- Legacy mirrors stay synced
- Tissue impulse from dose
"""

from datetime import datetime, timedelta, timezone

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.state_update import (
    apply_benchmark_observation,
    update_athlete_state,
    _adaptation_efficiency,
)
from app.schemas.engine_vectors import (
    AdaptationContribution,
    CapacityState,
    FatigueState,
    TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog
from app.schemas.engine_vectors import StressDoseSix


def _state(
    *,
    cns: float = 0.0,
    muscular: float = 0.0,
    metabolic: float = 0.0,
    structural: float = 0.0,
    tendon: float = 0.0,
    grip: float = 0.0,
    max_strength: float = 50.0,
    aerobic: float = 300.0,
    skill: float = 50.0,
    hypertrophy: float = 50.0,
    **_,  # absorb extra kwargs for convenience
) -> UnifiedStateVector:
    cx = CapacityState(
        aerobic=aerobic,
        max_strength=max_strength,
        hypertrophy=hypertrophy,
        skill=skill,
    )
    f = FatigueState(
        cns=cns, muscular=muscular, metabolic=metabolic,
        structural=structural, tendon=tendon, grip=grip,
    )
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(timezone.utc),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        **leg,
    )


def _dose(
    *,
    max_strength: float = 0.0,
    aerobic: float = 0.0,
    skill: float = 0.0,
    hypertrophy: float = 0.0,
    power: float = 0.0,
    metabolic_six: float = 0.5,
) -> StressDose:
    six = StressDoseSix(
        volume=0.5,
        intensity=0.5,
        density=0.4,
        impact=0.3,
        skill=0.2,
        metabolic=metabolic_six,
    )
    ac = AdaptationContribution(
        max_strength=max_strength,
        aerobic=aerobic,
        skill=skill,
        hypertrophy=hypertrophy,
        power=power,
    )
    return StressDose(
        dose_six=six,
        adaptation_contribution=ac,
        d_met_systemic=1.0,
        d_nm_peripheral=1.0,
        d_nm_central=1.0,
        d_struct_damage=0.5,
        d_struct_signal=2.0,
    )


def _log() -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(timezone.utc),
        modality="Strength",
        duration_minutes=60.0,
        session_rpe=7.0,
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )


# ---------------------------------------------------------------------------
# Adaptation gains
# ---------------------------------------------------------------------------

def test_strength_dose_increases_max_strength():
    s0 = _state(max_strength=50.0)
    d = _dose(max_strength=5.0)
    s1 = update_athlete_state(s0, d, timedelta(days=1), _log())
    assert s1.capacity_x.max_strength > s0.capacity_x.max_strength


def test_aerobic_dose_increases_aerobic_capacity():
    s0 = _state(aerobic=300.0)
    d = _dose(aerobic=5.0)
    s1 = update_athlete_state(s0, d, timedelta(days=1), _log())
    assert s1.capacity_x.aerobic > s0.capacity_x.aerobic


def test_high_fatigue_suppresses_adaptation():
    """Gains under high fatigue should be smaller than under low fatigue.

    Use timedelta(0) to prevent any decay before adaptation runs, and saturate
    enough axes so the mean clearly exceeds the suppression threshold (45).
    """
    s_fresh = _state(max_strength=50.0, cns=0.0, muscular=0.0)
    # 6 axes, all high → mean ≈ 85 >> threshold of 45
    s_tired = _state(
        max_strength=50.0,
        cns=85.0, muscular=85.0, metabolic=85.0,
        structural=85.0, tendon=85.0, grip=85.0,
    )
    d = _dose(max_strength=5.0)
    log = _log()

    s1_fresh = update_athlete_state(s_fresh, d, timedelta(seconds=0), log)
    s1_tired = update_athlete_state(s_tired, d, timedelta(seconds=0), log)

    gain_fresh = s1_fresh.capacity_x.max_strength - s_fresh.capacity_x.max_strength
    gain_tired = s1_tired.capacity_x.max_strength - s_tired.capacity_x.max_strength

    assert gain_fresh > gain_tired, "Fresh athlete should gain more than tired athlete"


def test_cns_fatigue_suppresses_skill_gains():
    """High CNS fatigue should suppress skill-axis adaptation specifically."""
    s_low_cns = _state(skill=40.0, cns=10.0)
    s_high_cns = _state(skill=40.0, cns=85.0)
    d = _dose(skill=5.0)
    log = _log()

    s1_low = update_athlete_state(s_low_cns, d, timedelta(hours=1), log)
    s1_high = update_athlete_state(s_high_cns, d, timedelta(hours=1), log)

    gain_low = s1_low.capacity_x.skill - s_low_cns.capacity_x.skill
    gain_high = s1_high.capacity_x.skill - s_high_cns.capacity_x.skill

    assert gain_low > gain_high, "Skill gains should be suppressed under high CNS fatigue"


def test_adaptation_efficiency_low_under_high_fatigue():
    """_adaptation_efficiency should return < 1.0 under high fatigue."""
    from app.engine.parameters import default_parameters
    s = _state(cns=70.0, muscular=80.0, metabolic=70.0, structural=60.0)
    p = default_parameters()
    eff = _adaptation_efficiency(s, p)
    assert eff < 1.0


def test_adaptation_efficiency_full_when_fresh():
    """_adaptation_efficiency should return 1.0 when fatigue is below threshold."""
    from app.engine.parameters import default_parameters
    s = _state(cns=5.0, muscular=5.0, metabolic=5.0)
    p = default_parameters()
    eff = _adaptation_efficiency(s, p)
    assert eff == 1.0


# ---------------------------------------------------------------------------
# Tissue impulses
# ---------------------------------------------------------------------------

def test_heavy_impact_increases_tissue_stress():
    s0 = _state()
    d = StressDose(
        dose_six=StressDoseSix(volume=2.0, intensity=1.0, density=0.8, impact=3.0, skill=0.2, metabolic=0.5),
        adaptation_contribution=AdaptationContribution(),
        d_met_systemic=2.0,
        d_nm_peripheral=2.0,
        d_nm_central=1.0,
        d_struct_damage=3.0,
        d_struct_signal=5.0,
    )
    log = WorkoutLog(
        timestamp=datetime.now(timezone.utc),
        modality="Running",
        duration_minutes=45.0,
        session_rpe=8.0,
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )
    s1 = update_athlete_state(s0, d, timedelta(days=1), log)
    # Running → ankle/knee tissue should increase
    assert s1.tissue_t.ankle > s0.tissue_t.ankle or s1.tissue_t.knee > s0.tissue_t.knee


# ---------------------------------------------------------------------------
# Fatigue decay
# ---------------------------------------------------------------------------

def test_fatigue_decays_over_time():
    s0 = _state(cns=50.0, muscular=40.0, metabolic=30.0)
    d = _dose()
    # Use a "rest" dose — no real training signal
    d_rest = StressDose(
        dose_six=StressDoseSix(),
        adaptation_contribution=AdaptationContribution(),
        d_met_systemic=0.0,
        d_nm_peripheral=0.0,
        d_nm_central=0.0,
        d_struct_damage=0.0,
        d_struct_signal=0.0,
    )
    log = _log()
    s1 = update_athlete_state(s0, d_rest, timedelta(days=5), log)
    assert s1.fatigue_f.cns < s0.fatigue_f.cns
    assert s1.fatigue_f.muscular < s0.fatigue_f.muscular


# ---------------------------------------------------------------------------
# Legacy mirror sync
# ---------------------------------------------------------------------------

def test_legacy_mirrors_sync_after_update():
    s0 = _state(max_strength=60.0)
    d = _dose(max_strength=2.0)
    s1 = update_athlete_state(s0, d, timedelta(days=1), _log())

    # c_nm_force should reflect updated max_strength
    assert s1.c_nm_force == s1.capacity_x.max_strength * 10.0


# ---------------------------------------------------------------------------
# Benchmark timestamp fix
# ---------------------------------------------------------------------------

from types import SimpleNamespace


def _mapping() -> SimpleNamespace:
    return SimpleNamespace(
        benchmark_definition_id=1,
        target_vector="capacity",
        target_key="max_strength",
        mapping_type="direct",
        coefficient=0.5,
        intercept=0.0,
        min_value=None,
        max_value=None,
        config={"scale": 100.0, "amp": 4.0},
    )


def test_benchmark_uses_observed_at_timestamp():
    obs_time = datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
    s0 = _state()
    s1 = apply_benchmark_observation(
        s0,
        raw_value=150.0,
        normalized_value=None,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[_mapping()],
        observed_at=obs_time,
    )
    assert s1.timestamp == obs_time, "Benchmark state timestamp must match observed_at"


def test_benchmark_without_observed_at_uses_utcnow():
    """When observed_at is None, timestamp should be set to current time (not prev state time)."""
    old_time = datetime(2023, 1, 1, tzinfo=timezone.utc)
    s0 = _state()
    s0 = s0.model_copy(update={"timestamp": old_time})

    s1 = apply_benchmark_observation(
        s0,
        raw_value=150.0,
        normalized_value=None,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[_mapping()],
        observed_at=None,
    )
    # Timestamp should be different from the old state time
    assert s1.timestamp > old_time


def test_benchmark_observation_state_ordered_chronologically():
    """Benchmark states from older tests should have older timestamps."""
    obs_old = datetime(2024, 3, 1, tzinfo=timezone.utc)
    obs_new = datetime(2024, 6, 1, tzinfo=timezone.utc)
    m = _mapping()

    s0 = _state()
    s_after_old = apply_benchmark_observation(
        s0, raw_value=100.0, normalized_value=None,
        better_direction="higher", observation_weight=1.0,
        mappings=[m], observed_at=obs_old,
    )
    s_after_new = apply_benchmark_observation(
        s_after_old, raw_value=130.0, normalized_value=None,
        better_direction="higher", observation_weight=1.0,
        mappings=[m], observed_at=obs_new,
    )

    assert s_after_old.timestamp == obs_old
    assert s_after_new.timestamp == obs_new
    assert s_after_new.timestamp > s_after_old.timestamp
