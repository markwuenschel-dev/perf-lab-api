"""Tests for the universal constraint rules (migrated from validate_session.py)."""

from app.logic.constraint_engine.constraints_impl import (
    universal_gymnastics_wrist_tissue,
    universal_olympic_metabolic_check,
)
from app.logic.constraint_engine.types import ConstraintContext


def _ctx(goal="Gymnastics", *, tissue=None, legacy=None, fatigue=None) -> ConstraintContext:
    return ConstraintContext(
        goal=goal,
        tissue_state=tissue or {},
        legacy=legacy or {},
        fatigue_state=fatigue or {},
    )


def test_gymnastics_wrist_hard_violation():
    ctx = _ctx("Gymnastics", tissue={"wrist": 80.0})
    result = universal_gymnastics_wrist_tissue({}, ctx)
    assert not result.passed
    assert result.severity.value == "hard"


def test_gymnastics_wrist_ok_below_threshold():
    ctx = _ctx("Gymnastics", tissue={"wrist": 60.0})
    result = universal_gymnastics_wrist_tissue({}, ctx)
    assert result.passed


def test_gymnastics_wrist_ignored_for_other_goals():
    ctx = _ctx("Strength", tissue={"wrist": 90.0})
    result = universal_gymnastics_wrist_tissue({}, ctx)
    assert result.passed


def test_olympic_soft_when_systemic_high():
    ctx = _ctx("OlympicLifts", legacy={"f_met_systemic": 70.0})
    result = universal_olympic_metabolic_check({"metabolic_emphasis": 0.6}, ctx)
    assert not result.passed
    assert result.severity.value == "soft"


def test_olympic_ok_when_metabolic_emphasis_low():
    ctx = _ctx("OlympicLifts", legacy={"f_met_systemic": 70.0})
    result = universal_olympic_metabolic_check({"metabolic_emphasis": 0.4}, ctx)
    assert result.passed
