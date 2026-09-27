"""The phase-8 fit policy: which shadow rows a fit may learn from, and how a pair is labelled.

The pairing rule is CAUSAL: a label is centred on the athlete's mean RPE over sessions strictly
before the one being labelled. The tests below would pass for a full-trajectory mean on most
inputs, so each one is built so that looking forward changes the answer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.logic.dose_fit_policy import (
    FIT_POLICY_VERSION,
    MAX_SESSION_GAP_DAYS,
    MIN_PRIOR_SESSIONS,
    LoggedSession,
    fit_tier,
    is_seeded_email,
    pair_sessions,
    pairing_rule,
)

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _s(i: int, day: float, rpe: float, user: int = 1) -> LoggedSession:
    return LoggedSession(
        workout_log_id=i, user_id=user, at=T0 + timedelta(days=day), session_rpe=rpe
    )


# --- tiers ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fabricated", "basis", "tier"),
    [
        (True, "sets_per_elapsed_minute", "fabricated_sets"),
        (False, "prescribed_timed_work_over_elapsed", "prescribed_proxy"),
        (False, "not_applicable", "density_not_modelled"),
        (False, None, "density_not_modelled"),
        (False, "some_future_basis", "density_not_modelled"),
        (False, "legacy_minutes_per_set", "density_not_modelled"),
        (False, "sets_per_elapsed_minute", "eligible"),
        (False, "performed_timed_work_over_elapsed", "eligible"),
    ],
)
def test_fit_tier(fabricated: bool, basis: str | None, tier: str) -> None:
    assert fit_tier(v0_volume_used_fabricated_sets=fabricated, v1_density_basis=basis) == tier


def test_fabricated_sets_outrank_an_eligible_basis() -> None:
    # A fabricated v0 side poisons the row whatever v1 measured.
    assert fit_tier(v0_volume_used_fabricated_sets=True, v1_density_basis="sets_per_elapsed_minute") == "fabricated_sets"


def test_seeded_accounts_are_recognised() -> None:
    assert is_seeded_email("demo+gf3@perflab.local")
    assert is_seeded_email("DEMO+pl1@PerfLab.Local")
    assert not is_seeded_email("athlete@example.com")
    assert not is_seeded_email(None)


# --- pairing ----------------------------------------------------------------------------


def _history(rpes: list[float], *, gap: float = 2.0) -> list[LoggedSession]:
    return [_s(i + 1, i * gap, r) for i, r in enumerate(rpes)]


def test_the_baseline_uses_only_earlier_sessions() -> None:
    # Sessions 1-3 at RPE 5, then 4 at RPE 5, then a very hard 5th (RPE 10).
    pairs = pair_sessions(_history([5, 5, 5, 5, 10]))
    p = pairs[4]
    assert p.status == "labelled"
    assert p.n_prior_sessions == 3
    assert p.causal_baseline_rpe == pytest.approx(5.0)
    # The label is the NEXT RPE against the PAST mean: 10 - 5.
    assert p.label == pytest.approx(5.0)
    # A full-trajectory mean would have been 6.0, giving a label of 4.0.
    assert p.label != pytest.approx(10 - 6.0)


def test_later_sessions_never_change_an_earlier_label() -> None:
    short = pair_sessions(_history([6, 4, 8, 5, 7]))
    long = pair_sessions(_history([6, 4, 8, 5, 7, 1, 1, 1, 10, 10]))
    for wl in (4,):
        assert short[wl].label == long[wl].label
        assert short[wl].causal_baseline_rpe == long[wl].causal_baseline_rpe


def test_insufficient_prior_history_is_unusable_not_borrowed_from_the_future() -> None:
    pairs = pair_sessions(_history([5, 6, 7, 8, 9]))
    for wl in range(1, MIN_PRIOR_SESSIONS + 1):
        assert pairs[wl].status == "insufficient_prior_history"
        assert pairs[wl].label is None
    assert pairs[1].causal_baseline_rpe is None
    assert pairs[MIN_PRIOR_SESSIONS + 1].status == "labelled"


def test_the_last_session_has_no_label() -> None:
    pairs = pair_sessions(_history([5, 5, 5, 5, 5]))
    assert pairs[5].status == "no_next_session"
    assert pairs[5].gap_days is None


def test_gap_window_is_floor_days_inclusive_of_both_ends() -> None:
    base = [_s(1, 0, 5), _s(2, 1, 5), _s(3, 2, 5), _s(4, 3, 5)]
    same_day = pair_sessions([*base, _s(5, 3.9, 6)])
    assert same_day[4].status == "same_day"
    assert same_day[4].gap_days == 0

    one = pair_sessions([*base, _s(5, 4.0, 6)])
    assert one[4].status == "labelled" and one[4].gap_days == 1

    edge = pair_sessions([*base, _s(5, 3 + MAX_SESSION_GAP_DAYS + 0.9, 6)])
    assert edge[4].status == "labelled" and edge[4].gap_days == MAX_SESSION_GAP_DAYS

    too_long = pair_sessions([*base, _s(5, 3 + MAX_SESSION_GAP_DAYS + 1, 6)])
    assert too_long[4].status == "gap_too_long"


def test_athletes_are_paired_independently() -> None:
    a = [_s(i, i, 5, user=1) for i in range(1, 6)]
    b = [_s(100 + i, i + 0.5, 9, user=2) for i in range(1, 6)]
    pairs = pair_sessions([*b, *a])  # input order must not matter
    assert pairs[4].causal_baseline_rpe == pytest.approx(5.0)
    assert pairs[104].causal_baseline_rpe == pytest.approx(9.0)
    assert pairs[4].next_session_rpe == 5 and pairs[104].next_session_rpe == 9


def test_equal_timestamps_order_by_workout_log_id() -> None:
    sessions = [_s(2, 0, 7), _s(1, 0, 3)]
    pairs = pair_sessions(sessions)
    assert pairs[1].next_session_rpe == 7
    assert pairs[2].status == "no_next_session"


def test_the_pairing_rule_is_recorded_with_the_policy_version() -> None:
    rule = pairing_rule()
    assert rule["baseline"] == "mean_rpe_of_strictly_earlier_sessions"
    assert rule["min_prior_sessions"] == MIN_PRIOR_SESSIONS
    assert rule["gap_days"] == [1, MAX_SESSION_GAP_DAYS]
    assert FIT_POLICY_VERSION.startswith("fit-policy-")
