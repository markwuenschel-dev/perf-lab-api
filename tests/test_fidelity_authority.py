"""Effort fidelity is an evidence-authority ladder — and it must fail CLOSED.

CONTEXT.md ("Effort fidelity", ADR-0045) documents a strict ordering:

    set_level > group_level > session_level > missing

Two properties follow, and neither may be left to a lookup default:

1. **Monotonicity** — every fidelity multiplier is *strictly* decreasing down the
   ladder. A rung that is documented as less trustworthy may never be scored as
   more trustworthy.
2. **Fail-closed on the unknown** — a fidelity string the code does not recognize
   is, by definition, unproven provenance. It must collapse to the MOST
   conservative rung, never silently inherit ``set_level`` authority.

These are asserted against the *public* surfaces production actually calls
(``external_intensity_for_set``, ``is_e1rm_informative``), not private helpers.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.logic import strength_calibration as sc
from app.logic import strength_evidence as se

# The documented ladder, most trustworthy first (CONTEXT.md "Effort fidelity").
FIDELITY_LADDER: tuple[str, ...] = ("set_level", "group_level", "session_level", "missing")

# Any string that is not a recognized rung. Unproven provenance.
unknown_fidelity = st.text(min_size=0, max_size=24).filter(lambda s: s not in FIDELITY_LADDER)


# ── the multiplier tables are monotone in the documented order ────────────────

@pytest.mark.parametrize(
    "table_name, table",
    [
        ("strength_calibration.FIDELITY_CONF_MULTIPLIER", sc.FIDELITY_CONF_MULTIPLIER),
        ("strength_evidence.FIDELITY_MULTIPLIER", se.FIDELITY_MULTIPLIER),
    ],
)
def test_fidelity_multiplier_table_is_monotone(table_name: str, table: dict[str, float]) -> None:
    """Every documented rung is present and STRICTLY less trusted than the one above."""
    assert set(table) == set(FIDELITY_LADDER), f"{table_name} does not cover the ladder"
    values = [table[rung] for rung in FIDELITY_LADDER]
    for upper, lower, upper_v, lower_v in zip(
        FIDELITY_LADDER[:-1], FIDELITY_LADDER[1:], values[:-1], values[1:], strict=True
    ):
        assert upper_v > lower_v, (
            f"{table_name}: {upper}={upper_v} must be strictly MORE trusted than "
            f"{lower}={lower_v} (CONTEXT.md ladder)"
        )


def test_conf_multipliers_are_sane_fractions() -> None:
    """Multipliers scale a base confidence: within [0, 1], and set_level is the ceiling."""
    for rung, value in sc.FIDELITY_CONF_MULTIPLIER.items():
        assert 0.0 <= value <= 1.0, f"{rung}={value} is not a fraction"
    assert sc.FIDELITY_CONF_MULTIPLIER["set_level"] == 1.0


# ── unknown fidelity fails CLOSED through the live dose path ──────────────────

def _chart_confidence(effort_fidelity: str) -> float:
    """Confidence of the RPE/RIR-chart rung for a given effort fidelity."""
    return sc.external_intensity_for_set(
        reps=5, load_kg=None, rpe=8.0, rir=None, e1rm_pre=None,
        to_failure=False, effort_fidelity=effort_fidelity,
    ).confidence


@given(fidelity=unknown_fidelity)
def test_unknown_fidelity_gets_the_most_conservative_confidence(fidelity: str) -> None:
    """An unrecognized fidelity may never score above the worst documented rung."""
    worst = min(sc.FIDELITY_CONF_MULTIPLIER.values())
    assert _chart_confidence(fidelity) == pytest.approx(
        round(sc._CONF_RPE_RIR_CHART * worst, 4)
    ), f"unknown fidelity {fidelity!r} did not collapse to the most conservative rung"


def test_dose_confidence_is_monotone_down_the_ladder() -> None:
    """The live dose path's confidence strictly decreases down the documented ladder."""
    confidences = [_chart_confidence(rung) for rung in FIDELITY_LADDER]
    assert confidences == sorted(confidences, reverse=True)
    assert len(set(confidences)) == len(confidences), "rungs must be distinguishable"


def test_missing_effort_is_less_trusted_than_session_level() -> None:
    """The regression under test: absent effort must NOT out-rank session-level effort."""
    assert _chart_confidence("missing") < _chart_confidence("session_level")


# ── the e1RM extraction gate defaults + fails closed ──────────────────────────

# A set that clears the standard bar (RPE 8) but NOT the stricter non-set_level bar (RPE 9).
_BORDERLINE = {"reps": 5.0, "rpe": 8.0, "rir": None}


def test_e1rm_gate_signature_default_is_conservative() -> None:
    """Omitting effort_fidelity must not silently grant set_level authority.

    An unstated fidelity is unproven provenance; it must be held to the stricter bar.
    """
    assert se.is_e1rm_informative(**_BORDERLINE) is False
    # Explicitly-proven set_level effort still clears the standard bar.
    assert se.is_e1rm_informative(**_BORDERLINE, effort_fidelity="set_level") is True


@given(fidelity=unknown_fidelity)
def test_e1rm_gate_unknown_fidelity_is_conservative(fidelity: str) -> None:
    """An unrecognized fidelity is held to the stricter bar, never the set_level bar."""
    assert se.is_e1rm_informative(**_BORDERLINE, effort_fidelity=fidelity) is False


@pytest.mark.parametrize("fidelity", ["group_level", "session_level", "missing"])
def test_e1rm_gate_below_set_level_needs_the_stricter_bar(fidelity: str) -> None:
    """Every rung below set_level clears only at RPE>=9 / RIR<=1 (ADR-0045)."""
    assert se.is_e1rm_informative(reps=5, rpe=8.0, rir=None, effort_fidelity=fidelity) is False
    assert se.is_e1rm_informative(reps=5, rpe=9.0, rir=None, effort_fidelity=fidelity) is True
