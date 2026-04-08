"""
Tests for the planning/block layer.

Verifies:
- Plan templates exist for powerlifting, running, gymnastics, grip
- Block-at-week resolution works correctly
- Deload triggers fire on high fatigue/tissue states
- Retest logic respects block completion
- weekly_session_distribution returns valid dicts
- Deload state overrides distribution to recovery sessions
"""

from datetime import datetime, timezone

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.planning import (
    BlockType,
    deload_triggered,
    get_block_progress,
    get_plan_template,
    retest_due,
    weekly_session_distribution,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state(
    *,
    cns: float = 0.0,
    muscular: float = 0.0,
    metabolic: float = 0.0,
    structural: float = 0.0,
    tendon: float = 0.0,
    lumbar: float = 0.0,
    knee: float = 0.0,
) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(
        cns=cns, muscular=muscular, metabolic=metabolic,
        structural=structural, tendon=tendon,
    )
    t = TissueState(lumbar=lumbar, knee=knee)
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


def _healthy() -> UnifiedStateVector:
    return _state()


# ---------------------------------------------------------------------------
# Template existence
# ---------------------------------------------------------------------------

def test_powerlifting_template_exists():
    tmpl = get_plan_template("Powerlifting")
    assert tmpl is not None
    assert tmpl.domain == "powerlifting"
    assert len(tmpl.blocks) >= 3


def test_running_template_exists():
    tmpl = get_plan_template("Running")
    assert tmpl is not None
    assert len(tmpl.blocks) >= 3


def test_half_marathon_template_exists():
    tmpl = get_plan_template("HalfMarathon")
    assert tmpl is not None


def test_gymnastics_template_exists():
    tmpl = get_plan_template("Gymnastics")
    assert tmpl is not None
    assert len(tmpl.blocks) >= 2


def test_grip_template_exists():
    tmpl = get_plan_template("Grip")
    assert tmpl is not None
    assert any(b.block_type == BlockType.GRIP_TISSUE for b in tmpl.blocks)


def test_olympic_lifting_template_exists():
    tmpl = get_plan_template("OlympicLifts")
    assert tmpl is not None
    assert any(b.block_type == BlockType.TECHNIQUE for b in tmpl.blocks)


def test_no_template_for_metcon_returns_none():
    tmpl = get_plan_template("MetCon")
    assert tmpl is None


# ---------------------------------------------------------------------------
# Block-at-week resolution
# ---------------------------------------------------------------------------

def test_pl_week_1_is_accumulation():
    tmpl = get_plan_template("Powerlifting")
    block = tmpl.block_at_week(1)
    assert block.block_type == BlockType.ACCUMULATION


def test_pl_week_5_is_intensification():
    tmpl = get_plan_template("Powerlifting")
    block = tmpl.block_at_week(5)
    assert block.block_type == BlockType.INTENSIFICATION


def test_pl_week_10_is_peak():
    tmpl = get_plan_template("Powerlifting")
    block = tmpl.block_at_week(10)
    assert block.block_type == BlockType.PEAK


def test_pl_week_12_is_deload():
    tmpl = get_plan_template("Powerlifting")
    block = tmpl.block_at_week(12)
    assert block.block_type == BlockType.DELOAD


def test_total_weeks_pl_is_12():
    tmpl = get_plan_template("Powerlifting")
    assert tmpl.total_weeks() == 12


# ---------------------------------------------------------------------------
# Deload trigger
# ---------------------------------------------------------------------------

def test_deload_not_triggered_when_fresh():
    s = _healthy()
    assert not deload_triggered(s)


def test_deload_triggered_on_high_fatigue_axis():
    s = _state(cns=65.0)
    assert deload_triggered(s)


def test_deload_triggered_on_high_mean_fatigue():
    # All 6 fatigue axes at ~47 → mean ≈ 47 > threshold of 45, but no single axis > 60
    s = _state(cns=47.0, muscular=47.0, metabolic=47.0, structural=47.0, tendon=47.0)
    # FatigueState has 6 keys; grip defaults to 0, mean = (47*5)/6 ≈ 39.2 — use higher values
    # Set all accessible axes high enough: cns=50, muscular=50, metabolic=50, structural=50, tendon=50
    # grip=0 (not in _state helper) → mean = (50*5)/6 ≈ 41.7 — still below 45
    # Need to also set grip or use the state directly
    from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
    from app.engine.state_bridge import sync_legacy_from_vectors
    cx = CapacityState()
    f = FatigueState(cns=50.0, muscular=50.0, metabolic=50.0, structural=50.0, tendon=50.0, grip=50.0)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    from datetime import datetime, timezone
    s2 = UnifiedStateVector(
        timestamp=datetime.now(timezone.utc),
        capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.5, skill_state={}, **leg,
    )
    assert deload_triggered(s2)


def test_deload_triggered_on_high_tissue():
    s = _state(lumbar=60.0)
    assert deload_triggered(s)


def test_deload_not_triggered_on_moderate_values():
    s = _state(cns=30.0, muscular=25.0, lumbar=20.0)
    assert not deload_triggered(s)


# ---------------------------------------------------------------------------
# Retest logic
# ---------------------------------------------------------------------------

def test_retest_due_when_block_complete():
    tmpl = get_plan_template("Powerlifting")
    peak_block = next(b for b in tmpl.blocks if b.block_type == BlockType.PEAK)
    sessions_target = peak_block.duration_weeks * 4
    assert retest_due(peak_block, int(sessions_target * 0.9))


def test_retest_not_due_early_in_block():
    tmpl = get_plan_template("Powerlifting")
    peak_block = next(b for b in tmpl.blocks if b.block_type == BlockType.PEAK)
    assert not retest_due(peak_block, 1)


def test_retest_not_due_for_block_without_retest_flag():
    tmpl = get_plan_template("Powerlifting")
    accum = tmpl.blocks[0]  # accumulation has retest_at_end=False
    assert not retest_due(accum, 100)


# ---------------------------------------------------------------------------
# Block progress
# ---------------------------------------------------------------------------

def test_block_progress_returns_for_powerlifting():
    s = _healthy()
    progress = get_block_progress(s, "Powerlifting", cycle_week=1)
    assert progress is not None
    assert progress.current_block.block_type == BlockType.ACCUMULATION
    assert progress.week_in_block == 1


def test_block_progress_deload_recommended_when_fatigued():
    s = _state(cns=70.0)
    progress = get_block_progress(s, "Powerlifting", cycle_week=3)
    assert progress is not None
    assert progress.deload_recommended


def test_block_progress_returns_none_for_no_template():
    s = _healthy()
    progress = get_block_progress(s, "MetCon", cycle_week=1)
    assert progress is None


# ---------------------------------------------------------------------------
# Weekly distribution
# ---------------------------------------------------------------------------

def test_weekly_distribution_powerlifting_week_1():
    s = _healthy()
    dist = weekly_session_distribution(s, "Powerlifting", cycle_week=1)
    assert isinstance(dist, dict)
    assert len(dist) >= 1
    # Should include some SBD sessions
    assert any("Strength" in k or "SBD" in k for k in dist)


def test_weekly_distribution_deload_override():
    """High fatigue state should override to recovery sessions."""
    s = _state(cns=70.0, muscular=65.0)
    dist = weekly_session_distribution(s, "Powerlifting", cycle_week=3)
    # Should redirect to recovery / technique
    assert any("Recovery" in k or "Technique" in k for k in dist)


def test_weekly_distribution_fallback_for_no_template():
    s = _healthy()
    dist = weekly_session_distribution(s, "General")
    assert isinstance(dist, dict)
    assert len(dist) >= 1
