"""Forward-projection engine tests (Phase 7 — goal-anchored program).

Non-DB: exercises the pure ``project_trajectory`` (state_in + params -> response)
directly, so this module runs locally and in CI regardless of DB availability
(see tests/test_projection_routes.py for the DB-gated round trip).
"""

from app.domain.vectors import CapacityState
from app.engine.simulate import baseline_state
from app.schemas.projection import ProjectionRequest
from app.services.projection_service import project_trajectory


def _req(**kw) -> ProjectionRequest:
    params = {
        "goal": "Powerlifting",
        "weeks": 8,
        "weekly_volume": 60,
        "intensity": "balanced",
        "recovery": "standard",
    }
    params.update(kw)
    return ProjectionRequest(**params)


def test_response_shape_and_series_lengths():
    state = baseline_state(max_strength=70.0, aerobic=320.0)
    resp = project_trajectory(state, _req(weeks=6))

    assert resp.weeks == 6
    assert resp.goal == "Powerlifting"
    # Exactly 8 axes, canonical order.
    assert [a.key for a in resp.axes] == list(CapacityState.KEYS)
    for axis in resp.axes:
        assert len(axis.series) == 7  # weeks + 1
        assert len(axis.baseline_series) == 7
        assert axis.start == axis.series[0]
        assert axis.projected == axis.series[-1]
        assert axis.baseline == axis.baseline_series[-1]
    assert len(resp.readiness_series) == 7
    assert 0.0 <= resp.peak_fatigue <= 100.0


def test_axes_have_human_labels():
    resp = project_trajectory(baseline_state(), _req(weeks=2))
    labels = {a.key: a.label for a in resp.axes}
    assert labels["max_strength"] == "Max strength"
    assert labels["work_capacity"] == "Work capacity"


def _axis(resp, key):
    return next(a for a in resp.axes if a.key == key)


def test_higher_volume_strength_plan_beats_maintain_baseline():
    """A hard, high-volume Powerlifting plan grows max_strength above maintain."""
    state = baseline_state(max_strength=70.0)
    resp = project_trajectory(
        state,
        _req(goal="Powerlifting", weeks=10, weekly_volume=85, intensity="hard"),
    )
    ms = _axis(resp, "max_strength")
    assert ms.projected > ms.baseline
    assert ms.projected > ms.start


def test_running_plan_grows_aerobic():
    """A running plan drives the aerobic axis up (and above the maintain baseline)."""
    state = baseline_state(aerobic=320.0, max_strength=70.0)
    resp = project_trajectory(
        state,
        _req(goal="Running", weeks=10, weekly_volume=80, intensity="balanced"),
    )
    aero = _axis(resp, "aerobic")
    assert aero.projected > aero.start
    assert aero.projected > aero.baseline


def test_goal_specificity_running_vs_powerlifting():
    """Different goals steer growth toward different axes."""
    state = baseline_state(aerobic=320.0, max_strength=70.0)
    run = project_trajectory(
        state, _req(goal="Running", weeks=10, weekly_volume=80)
    )
    lift = project_trajectory(
        state, _req(goal="Powerlifting", weeks=10, weekly_volume=80, intensity="hard")
    )
    # Running builds more aerobic than a powerlifting plan does.
    assert _axis(run, "aerobic").projected > _axis(lift, "aerobic").projected
    # Powerlifting builds more max_strength than a running plan does.
    assert _axis(lift, "max_strength").projected > _axis(run, "max_strength").projected


def test_poor_recovery_raises_peak_fatigue():
    """Minimal recovery accumulates more fatigue than high recovery."""
    state = baseline_state(max_strength=70.0)
    high = project_trajectory(
        state, _req(goal="Powerlifting", weeks=8, weekly_volume=80, recovery="high")
    )
    minimal = project_trajectory(
        state, _req(goal="Powerlifting", weeks=8, weekly_volume=80, recovery="minimal")
    )
    assert minimal.peak_fatigue > high.peak_fatigue


def test_detraining_maintain_baseline_does_not_explode():
    """The maintain baseline stays sane (no NaN / negative capacity)."""
    resp = project_trajectory(baseline_state(max_strength=70.0), _req(weeks=12))
    ms = _axis(resp, "max_strength")
    for v in ms.baseline_series:
        assert v >= 0.0
    # Readiness stays within bounds across the horizon.
    assert all(0.0 <= r <= 100.0 for r in resp.readiness_series)


def test_no_history_default_state_does_not_crash():
    """A brand-new athlete (all-default capacities) projects without error."""
    resp = project_trajectory(baseline_state(), _req(weeks=4, weekly_volume=50))
    assert len(resp.axes) == 8
    assert len(resp.readiness_series) == 5


def test_weeks_are_clamped_defensively():
    """Out-of-range weeks are clamped inside the pure function (belt-and-suspenders)."""
    state = baseline_state()
    req = _req()
    object.__setattr__(req, "weeks", 40)  # bypass Field validation to test the clamp
    resp = project_trajectory(state, req)
    assert resp.weeks == 16
    assert len(resp.readiness_series) == 17
