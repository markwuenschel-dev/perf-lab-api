"""Value-pinning tests for the readiness KPI-flag thresholds — no DB required.

``dashboard_service.readiness_payload`` raises a soft flag when a KPI crosses a
fixed numeric threshold. Nothing pinned those numbers: a refactor could have
transposed ``14.0`` to ``41.0``, or relaxed a strict ``>`` into ``>=``, and every
existing test would still have gone green (the route tests only assert that the
``kpi_flags`` key exists).

These tests pin each threshold *and its strictness* by asserting the flag flips
exactly at the boundary — the value on the boundary produces no flag, one tick
past it produces exactly one flag with an exact key name. The DB seam
(``load_current_state_for_display`` / ``latest_kpi_values``) is monkeypatched, so this is a
pure characterization of the flag logic and runs without Postgres.
"""

from app.services import dashboard_service

_STATE = object()  # readiness_payload passes the state through untouched.


def _install(monkeypatch, kpi_values: dict[str, float]) -> None:
    """Patch the two DB-backed seams readiness_payload consumes."""

    async def _state(db, user_id):
        return _STATE

    async def _kpis(db, user_id):
        return dict(kpi_values)

    monkeypatch.setattr(dashboard_service, "load_current_state_for_display", _state)
    monkeypatch.setattr(dashboard_service, "latest_kpi_values", _kpis)


async def _flags_for(monkeypatch, **kpi_values: float) -> dict[str, object]:
    _install(monkeypatch, kpi_values)
    state, flags = await dashboard_service.readiness_payload(object(), user_id=1)
    assert state is _STATE, "state must be returned unchanged alongside the flags"
    return flags


# --- run_fatigue_factor: flags when strictly ABOVE 14.0 --------------------------


async def test_run_fatigue_factor_exactly_at_threshold_does_not_flag(monkeypatch):
    assert await _flags_for(monkeypatch, run_fatigue_factor=14.0) == {}


async def test_run_fatigue_factor_just_above_threshold_flags(monkeypatch):
    flags = await _flags_for(monkeypatch, run_fatigue_factor=14.01)
    assert flags == {"run_fatigue_factor_elevated": True}


async def test_run_fatigue_factor_well_below_threshold_does_not_flag(monkeypatch):
    assert await _flags_for(monkeypatch, run_fatigue_factor=13.99) == {}


# --- pl_relative_total: flags when strictly BELOW 3.0 ----------------------------


async def test_pl_relative_total_exactly_at_threshold_does_not_flag(monkeypatch):
    assert await _flags_for(monkeypatch, pl_relative_total=3.0) == {}


async def test_pl_relative_total_just_below_threshold_flags(monkeypatch):
    flags = await _flags_for(monkeypatch, pl_relative_total=2.99)
    assert flags == {"pl_relative_total_low": True}


async def test_pl_relative_total_just_above_threshold_does_not_flag(monkeypatch):
    assert await _flags_for(monkeypatch, pl_relative_total=3.01) == {}


# --- wl_snatch_cj_ratio: flags when strictly BELOW 72.0 --------------------------


async def test_wl_snatch_cj_ratio_exactly_at_threshold_does_not_flag(monkeypatch):
    assert await _flags_for(monkeypatch, wl_snatch_cj_ratio=72.0) == {}


async def test_wl_snatch_cj_ratio_just_below_threshold_flags(monkeypatch):
    flags = await _flags_for(monkeypatch, wl_snatch_cj_ratio=71.99)
    assert flags == {"wl_snatch_share_low": True}


async def test_wl_snatch_cj_ratio_just_above_threshold_does_not_flag(monkeypatch):
    assert await _flags_for(monkeypatch, wl_snatch_cj_ratio=72.01) == {}


# --- combinations / absence ------------------------------------------------------


async def test_no_kpis_produces_no_flags(monkeypatch):
    assert await _flags_for(monkeypatch) == {}


async def test_all_three_thresholds_crossed_raises_all_three_flags(monkeypatch):
    flags = await _flags_for(
        monkeypatch,
        run_fatigue_factor=14.01,
        pl_relative_total=2.99,
        wl_snatch_cj_ratio=71.99,
    )
    assert flags == {
        "run_fatigue_factor_elevated": True,
        "pl_relative_total_low": True,
        "wl_snatch_share_low": True,
    }


async def test_all_three_exactly_on_their_boundaries_raises_nothing(monkeypatch):
    """The one case that catches a transposed digit AND a loosened comparison."""
    flags = await _flags_for(
        monkeypatch,
        run_fatigue_factor=14.0,
        pl_relative_total=3.0,
        wl_snatch_cj_ratio=72.0,
    )
    assert flags == {}
