"""Prescriber signal derivation (Phase 5 — macrocycle spine).

Non-DB: exercises the pure ``signals_from_anchor`` / ``signals_from_scan``
helpers (app.services.objective_service) directly, so this runs locally and in
CI regardless of DB availability. The DB-touching routing between anchor and
scan is covered by the standalone Postgres harness (Task 3) and mirrored in the
DB-gated route tests.
"""
from datetime import date, timedelta

from app.models.objective import Objective
from app.services.objective_service import (
    OBJECTIVE_TAPER_WINDOW_DAYS,
    signals_from_anchor,
    signals_from_scan,
)

TODAY = date(2026, 7, 4)


def _obj(**kwargs: object) -> Objective:
    """A detached (unpersisted) Objective for pure-logic tests."""
    defaults: dict[str, object] = {
        "id": 1, "user_id": 1, "label": "x", "domain": None, "target_date": None, "priority": 3
    }
    defaults.update(kwargs)
    return Objective(**defaults)


# ---------------------------------------------------------------------------
# signals_from_anchor — the macrocycle-anchored path
# ---------------------------------------------------------------------------

def test_anchor_near_target_date_tapers():
    anchor = _obj(domain="running", target_date=TODAY + timedelta(days=OBJECTIVE_TAPER_WINDOW_DAYS))
    sig = signals_from_anchor(anchor, today=TODAY)
    assert sig == {"taper": True, "domain": "running"}


def test_anchor_just_outside_window_does_not_taper():
    anchor = _obj(
        domain="strength", target_date=TODAY + timedelta(days=OBJECTIVE_TAPER_WINDOW_DAYS + 1)
    )
    sig = signals_from_anchor(anchor, today=TODAY)
    assert sig == {"taper": False, "domain": "strength"}


def test_anchor_past_target_date_does_not_taper():
    anchor = _obj(domain="running", target_date=TODAY - timedelta(days=1))
    assert signals_from_anchor(anchor, today=TODAY)["taper"] is False


def test_anchor_no_target_date_does_not_taper_but_keeps_domain():
    anchor = _obj(domain="hypertrophy", target_date=None)
    assert signals_from_anchor(anchor, today=TODAY) == {"taper": False, "domain": "hypertrophy"}


def test_anchor_domain_wins_over_other_objectives():
    """The anchor's domain is used even if it is lower priority than others —
    this is the whole point of the spine (the program goal drives emphasis)."""
    anchor = _obj(domain="running", priority=5, target_date=None)
    assert signals_from_anchor(anchor, today=TODAY)["domain"] == "running"


# ---------------------------------------------------------------------------
# signals_from_scan — the legacy fallback (no active macrocycle)
# ---------------------------------------------------------------------------

def test_scan_empty_is_no_signal():
    assert signals_from_scan([], today=TODAY) == {"taper": False, "domain": None}


def test_scan_nearest_upcoming_drives_taper():
    near = _obj(id=1, domain="running", priority=2, target_date=TODAY + timedelta(days=5))
    far = _obj(id=2, domain="strength", priority=1, target_date=TODAY + timedelta(days=90))
    sig = signals_from_scan([far, near], today=TODAY)
    assert sig["taper"] is True
    # domain still comes from the highest priority (lowest number) objective
    assert sig["domain"] == "strength"


def test_scan_no_upcoming_target_no_taper():
    past = _obj(id=1, domain="running", priority=1, target_date=TODAY - timedelta(days=3))
    undated = _obj(id=2, domain="strength", priority=2, target_date=None)
    sig = signals_from_scan([past, undated], today=TODAY)
    assert sig["taper"] is False
    assert sig["domain"] == "running"


def test_scan_priority_ties_broken_by_lowest_id():
    a = _obj(id=7, domain="running", priority=1, target_date=None)
    b = _obj(id=3, domain="strength", priority=1, target_date=None)
    assert signals_from_scan([a, b], today=TODAY)["domain"] == "strength"


def test_scan_target_exactly_on_window_edge_tapers():
    o = _obj(id=1, domain="running", priority=1, target_date=TODAY + timedelta(days=OBJECTIVE_TAPER_WINDOW_DAYS))
    assert signals_from_scan([o], today=TODAY)["taper"] is True


def test_scan_target_today_tapers():
    o = _obj(id=1, domain="running", priority=1, target_date=TODAY)
    assert signals_from_scan([o], today=TODAY)["taper"] is True
