"""Pure-unit tests for the P8 honesty layer — registry, implicit tracking, and the
evidence-coverage confidence object (ADR-0052 / ADR-0053). No DB required."""

from app.logic.wellness_registry import (
    coverage_signals,
    provided_signals,
    signal_from_metric,
    signal_provided,
)
from app.logic.wellness_tracking import get_expected_tracked_signals
from app.services.readiness_service import (
    build_confidence,
    confidence_band,
    readiness_band,
)


class _Sample:
    """Lightweight stand-in for a WellnessSample row (attribute access only)."""

    def __init__(self, **metrics):
        for m in ("hrv_ms", "sleep_hours", "sleep_quality", "resting_hr", "soreness", "mood", "stress"):
            setattr(self, m, metrics.get(m))


# --- Registry -------------------------------------------------------------------

def test_sleep_is_one_logical_signal_backed_by_two_metrics():
    assert signal_from_metric("sleep_hours") == "sleep"
    assert signal_from_metric("sleep_quality") == "sleep"
    # Provided if EITHER metric is present (no double-count).
    assert signal_provided(_Sample(sleep_hours=7.5), "sleep")
    assert signal_provided(_Sample(sleep_quality=80.0), "sleep")
    assert not signal_provided(_Sample(), "sleep")


def test_stress_is_a_coverage_signal():
    assert "stress" in coverage_signals()
    assert signal_provided(_Sample(stress=4.0), "stress")


def test_provided_signals_is_logical():
    s = _Sample(sleep_hours=7.0, hrv_ms=60.0, soreness=2.0)
    assert provided_signals(s) == {"sleep", "hrv", "soreness"}


# --- Implicit tracking ----------------------------------------------------------

def test_never_provided_signal_is_untracked_by_default():
    # A device-less athlete who has only ever logged sleep/soreness/mood.
    expected = get_expected_tracked_signals({"sleep", "soreness", "mood"})
    assert expected == {"sleep", "soreness", "mood"}
    assert "hrv" not in expected and "rhr" not in expected


def test_explicit_untracked_removes_even_if_previously_logged():
    expected = get_expected_tracked_signals(
        {"sleep", "hrv"}, explicitly_untracked={"hrv"}
    )
    assert expected == {"sleep"}


def test_explicit_opt_in_adds_before_first_log():
    expected = get_expected_tracked_signals(set(), explicitly_tracked={"stress"})
    assert expected == {"stress"}


# --- Bands ----------------------------------------------------------------------

def test_readiness_bands():
    assert readiness_band(80) == "high"
    assert readiness_band(65) == "good"
    assert readiness_band(50) == "moderate"
    assert readiness_band(20) == "low"


def test_confidence_bands():
    assert confidence_band(0.9) == "high"
    assert confidence_band(0.5) == "medium"
    assert confidence_band(0.2) == "low"


# --- Confidence object ----------------------------------------------------------

def _conf(**overrides):
    base = {
        "has_load_model": True,
        "expected": {"sleep", "hrv", "soreness"},
        "provided_today": {"sleep", "hrv", "soreness"},
        "fresh": True,
        "stale_signals": set(),
        "baseline_days": 30,
        "untracked": set(),
    }
    base.update(overrides)
    return build_confidence(**base)


def test_full_coverage_is_well_supported_high():
    c = _conf()
    assert c.band == "high"
    assert c.status == "well_supported"
    assert c.recommendation_gate.enforced is False
    assert c.recommendation_gate.max_recommendation_authority == "normal"
    assert c.signal_summary.unknown_today == []


def test_missing_tracked_signal_lowers_confidence_and_lists_gap():
    c = _conf(provided_today={"sleep", "soreness"})  # hrv tracked but missing today
    assert c.status == "partial_data"
    assert "hrv" in c.signal_summary.unknown_today
    assert "hrv_unknown_today" in c.reasons
    assert c.score < _conf().score  # strictly lower than full coverage


def test_untracked_signal_incurs_no_penalty():
    # HRV never expected: coverage is over {sleep, soreness}, both provided -> full.
    full = build_confidence(
        has_load_model=True,
        expected={"sleep", "soreness"},
        provided_today={"sleep", "soreness"},
        fresh=True,
        stale_signals=set(),
        baseline_days=30,
        untracked={"hrv", "rhr", "mood", "stress"},
    )
    assert full.status == "well_supported"
    assert "hrv" in full.signal_summary.untracked
    assert "hrv_untracked" in full.reasons
    # Not penalized: same score as if hrv simply didn't exist.
    assert full.score >= 0.9


def test_no_expected_signals_does_not_grant_free_coverage():
    # Tracks nothing: wellness term is not-applicable (dropped), not treated as 1.0.
    c = build_confidence(
        has_load_model=True,
        expected=set(),
        provided_today=set(),
        fresh=False,
        stale_signals=set(),
        baseline_days=0,
        untracked=set(coverage_signals()),
    )
    assert c.status == "sparse_data"
    # Only load contributes (0.35/0.65 renormalized); freshness+baseline are 0.
    assert c.score < 0.75


def test_stale_sample_flagged_and_authority_advisory_only():
    c = _conf(provided_today=set(), fresh=False, stale_signals={"sleep", "hrv"})
    assert c.status == "stale_data"
    assert c.signal_summary.stale == ["hrv", "sleep"]
    assert "checkin_stale" in c.reasons
    # Report-only regardless of authority tier.
    assert c.recommendation_gate.enforced is False
