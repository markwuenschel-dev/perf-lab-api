"""fatigue_from_legacy/tissue_from_legacy fail closed on a missing (NULL) scalar (ADR-0067).

A missing legacy fatigue/tissue scalar is missing evidence, not a known-zero value: these
shared bootstrap functions must raise a named, attributable error instead of letting a
None reach `min(100.0, None)` and fail incidentally with a bare TypeError.
"""
import pytest

from app.engine.state_bridge import (
    IncompleteLegacyState,
    fatigue_from_legacy,
    tissue_from_legacy,
)


def test_fatigue_from_legacy_still_works_with_all_values_present():
    f = fatigue_from_legacy(20.0, 15.0, 20.0, 10.0)
    assert f.metabolic == 20.0
    assert f.muscular == 15.0
    assert f.cns == 20.0


@pytest.mark.parametrize(
    "args",
    [
        (None, 15.0, 20.0, 10.0),
        (20.0, None, 20.0, 10.0),
        (20.0, 15.0, None, 10.0),
        (20.0, 15.0, 20.0, None),
    ],
)
def test_fatigue_from_legacy_raises_incomplete_legacy_state_on_any_missing_field(args):
    with pytest.raises(IncompleteLegacyState):
        fatigue_from_legacy(*args)


def test_fatigue_from_legacy_error_names_the_missing_field():
    with pytest.raises(IncompleteLegacyState) as exc_info:
        fatigue_from_legacy(None, 15.0, 20.0, 10.0)
    assert exc_info.value.field == "f_met_systemic"


def test_tissue_from_legacy_still_works_with_value_present():
    t = tissue_from_legacy(10.0)
    assert t.shoulder == pytest.approx(3.5)


def test_tissue_from_legacy_raises_incomplete_legacy_state_on_missing_value():
    with pytest.raises(IncompleteLegacyState) as exc_info:
        tissue_from_legacy(None)
    assert exc_info.value.field == "f_struct_damage"


def test_fatigue_from_legacy_never_silently_defaults_to_zero():
    """The whole point of this guard: a missing scalar must never resolve to 0.0."""
    with pytest.raises(IncompleteLegacyState):
        fatigue_from_legacy(None, None, None, None)
