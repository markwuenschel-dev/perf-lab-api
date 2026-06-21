"""Periodization intent envelope (ADR-0029).

week_number resolves to a phase + volume modifier + RPE target, and the prescriber
applies it — so a week-2 accumulation session and a week-11 peak session differ even
at equal state.
"""

from app.engine.simulate import baseline_state
from app.logic.planning import periodization_envelope
from app.logic.prescriber import recommend_next_session

# --- envelope resolution ---

def test_envelope_accumulation_early():
    e = periodization_envelope(12, 2, 4)
    assert e.phase == "accumulation"
    assert e.volume_modifier > 1.0


def test_envelope_intensification_mid():
    assert periodization_envelope(12, 7, 0).phase == "intensification"


def test_envelope_peak_late():
    e = periodization_envelope(12, 10, 0)
    assert e.phase == "peak"
    assert e.volume_modifier < 0.8


def test_envelope_deload_week():
    e = periodization_envelope(12, 4, 4)
    assert e.phase == "deload"
    assert e.volume_modifier <= 0.6


def test_envelope_taper_last_week():
    assert periodization_envelope(10, 10, 0).phase == "taper"


# --- prescriber applies the envelope ---

def _ctx(week, weeks=12, deload_n=0, **kw):
    return {"week_number": week, "duration_weeks": weeks, "deload_every_n_weeks": deload_n, **kw}


def test_prescriber_annotates_phase_and_rpe_target():
    rx = recommend_next_session(baseline_state(max_strength=50.0), goal="Strength", block_context=_ctx(2))
    assert rx.why is not None
    flags = rx.why.constraints_applied
    assert any("block:phase=accumulation" in c for c in flags)
    assert any("block:rpe_target=" in c for c in flags)


def test_accumulation_week_higher_volume_than_peak():
    s = baseline_state(max_strength=50.0)
    acc = recommend_next_session(s, goal="Strength", block_context=_ctx(2))
    peak = recommend_next_session(s, goal="Strength", block_context=_ctx(11))
    # Same base candidate at equal state; accumulation vol 1.15 > peak vol 0.7.
    assert acc.duration_min > peak.duration_min


def test_flagged_deload_week_uses_configured_factor():
    rx = recommend_next_session(
        baseline_state(max_strength=50.0), goal="Strength",
        block_context=_ctx(4, deload_n=4, is_deload=True, deload_volume_factor=0.5),
    )
    assert any("block:phase=deload(×0.50)" in c for c in rx.why.constraints_applied)
