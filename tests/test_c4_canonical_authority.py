"""INT-05 C4: decisions follow the canonical vectors, never the legacy scalar mirror.

Two kinds of proof, both with expected values written out by hand (not derived from the
production helpers):

* **Boundaries.** Every decision threshold that used to read a mirror is probed just below,
  at, and just above, on a state built from the canonical axis.
* **Canonical authority.** Each probe is repeated on a state whose legacy scalars deliberately
  say the OPPOSITE of the vectors. The outcome must follow the vectors. Before C4 these rules
  read the scalars, so every inconsistent case below would have flipped.

Rule ids, prescriber branch ids and the StateEvidence axis strings are asserted literally:
they are contracts and must not move with the read.
"""
from __future__ import annotations

import random
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.vectors import CapacityState, FatigueState, TissueState
from app.engine.state_bridge import default_engine_state_dict
from app.logic.constraint_engine import constraints_impl as ci
from app.logic.constraint_engine.context_builder import build_constraint_context
from app.logic.prescriber import _readiness_redirect, _safety_candidates
from app.logic.prescription_finalize import _derive_state_evidence
from app.schemas.state import UnifiedStateVector
from app.services import personalization_shadow_service as ps

_T = datetime(2026, 10, 21, tzinfo=UTC)

#: The opposite of anything a probe sets: every legacy scalar at an extreme that would
#: trip (or, for aerobic, un-trip) the rule it used to drive.
_MIRROR_SAYS_FIRE = {"f_met_systemic": 100.0, "f_nm_central": 100.0, "f_nm_peripheral": 100.0,
                     "c_met_aerobic": 0.0, "f_struct_damage": 100.0}
_MIRROR_SAYS_QUIET = {"f_met_systemic": 0.0, "f_nm_central": 0.0, "f_nm_peripheral": 0.0,
                      "c_met_aerobic": 500.0, "f_struct_damage": 0.0}


def _canonical(*, cns: float = 0.0, muscular: float = 0.0, metabolic: float = 0.0,
               aerobic: float = 300.0) -> UnifiedStateVector:
    """A state whose legacy scalars are hand-written to MATCH the vectors (no helper)."""
    return UnifiedStateVector(
        timestamp=_T,
        capacity_x=CapacityState(aerobic=aerobic),
        fatigue_f=FatigueState(cns=cns, muscular=muscular, metabolic=metabolic),
        tissue_t=TissueState(),
        # Defaults: max_strength 100 → 21*100+400; hypertrophy 50, work_capacity 50 →
        # max(1, 60+20); glycolytic 50 → 300*50; structural/tendon/grip/tissue 0 → 0.
        c_met_aerobic=aerobic, c_nm_force=2500.0, c_struct=80.0, b_met_anaerobic=15000.0,
        f_met_systemic=metabolic, f_nm_peripheral=muscular, f_nm_central=cns,
        f_struct_damage=0.0,
    )


def _inconsistent(state: UnifiedStateVector, *, fires: bool) -> UnifiedStateVector:
    """The same vectors, with every mirror claiming the opposite outcome."""
    return state.model_copy(update=_MIRROR_SAYS_QUIET if fires else _MIRROR_SAYS_FIRE)


def _variants(state: UnifiedStateVector, fires: bool) -> list[UnifiedStateVector]:
    return [state, _inconsistent(state, fires=fires)]


# ── prescriber ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("metabolic", "fires"), [(79.99, False), (80.0, False), (80.01, True)])
def test_systemic_safety_override_follows_the_metabolic_axis(metabolic: float, fires: bool) -> None:
    for s in _variants(_canonical(metabolic=metabolic), fires):
        ids = [c.branch_id for c in _safety_candidates(s)]
        assert ("safety_systemic_metabolic" in ids) is fires, (metabolic, s.f_met_systemic, ids)


@pytest.mark.parametrize(("cns", "fires"), [(59.99, False), (60.0, False), (60.01, True)])
def test_cns_redirect_follows_the_cns_axis(cns: float, fires: bool) -> None:
    for s in _variants(_canonical(cns=cns), fires):
        ids = {c.branch_id for c in _readiness_redirect(s, "Strength", {})}
        assert bool(ids & {"readiness_cns_aerobic_shift", "readiness_cns_technique"}) is fires, ids


@pytest.mark.parametrize(
    ("aerobic", "branch"),
    [(0.0, "readiness_cns_technique"), (0.01, "readiness_cns_aerobic_shift"),
     (300.0, "readiness_cns_aerobic_shift")],
)
def test_cns_redirect_branch_follows_the_aerobic_axis(aerobic: float, branch: str) -> None:
    s = _canonical(cns=70.0, aerobic=aerobic)
    # Mirror inverted: aerobic says 0 when the axis is populated, populated when it is 0.
    flipped = s.model_copy(update={"c_met_aerobic": 0.0 if aerobic > 0 else 500.0})
    for state in (s, flipped):
        ids = {c.branch_id for c in _readiness_redirect(state, "Strength", {})}
        assert branch in ids, (aerobic, state.c_met_aerobic, ids)


@pytest.mark.parametrize(("muscular", "fires"), [(59.99, False), (60.0, False), (60.01, True)])
def test_peripheral_redirect_follows_the_muscular_axis(muscular: float, fires: bool) -> None:
    for s in _variants(_canonical(muscular=muscular), fires):
        ids = {c.branch_id for c in _readiness_redirect(s, "Strength", {})}
        assert ("readiness_peripheral_active_recovery" in ids) is fires, ids
        ids_p = {c.branch_id for c in _readiness_redirect(s, "Power", {})}
        assert ("readiness_peripheral_neural_priming" in ids_p) is fires, ids_p


# ── evidence drivers (the axis strings are an API contract) ────────────────────

@pytest.mark.parametrize(
    ("over", "axis", "fires"),
    [
        ({"cns": 54.99}, "f_nm_central", False), ({"cns": 55.0}, "f_nm_central", False),
        ({"cns": 55.01}, "f_nm_central", True),
        ({"muscular": 54.99}, "f_nm_peripheral", False),
        ({"muscular": 55.0}, "f_nm_peripheral", False),
        ({"muscular": 55.01}, "f_nm_peripheral", True),
        ({"metabolic": 59.99}, "f_met_systemic", False),
        ({"metabolic": 60.0}, "f_met_systemic", False),
        ({"metabolic": 60.01}, "f_met_systemic", True),
        ({"aerobic": 0.0}, "c_met_aerobic", False),      # unpopulated, not low
        ({"aerobic": 0.01}, "c_met_aerobic", True),
        ({"aerobic": 29.99}, "c_met_aerobic", True),
        ({"aerobic": 30.0}, "c_met_aerobic", False),
        ({"aerobic": 30.01}, "c_met_aerobic", False),
    ],
)
def test_evidence_drivers_follow_the_canonical_axis(
    over: dict[str, float], axis: str, fires: bool
) -> None:
    s = _canonical(**over)
    if axis == "c_met_aerobic":
        # The mirror claims the opposite: low-but-populated when the axis is not, and back.
        variants = [s, s.model_copy(update={"c_met_aerobic": 500.0 if fires else 20.0})]
    else:
        variants = _variants(s, fires)
    for state in variants:
        ev = [e for e in _derive_state_evidence(state) if e.axis == axis]
        assert bool(ev) is fires, (over, axis, [e.axis for e in _derive_state_evidence(state)])
        if fires:
            # The evidence reports the CANONICAL value, not the mirror's.
            assert ev[0].value == pytest.approx(next(iter(over.values())))


# ── constraints (ids are contracts) ────────────────────────────────────────────

_HIGH = {"intensity_bucket": "high"}
_CASES: list[tuple[Any, str, str, dict[str, Any], dict[str, float], float, str]] = [
    # rule, rule id, goal, candidate, axis kwarg, threshold, direction
    (ci.universal_fatigue_ok, "universal_fatigue_ok", "Strength", {}, "metabolic", 80.0, ">"),
    (ci.universal_olympic_metabolic_check, "universal_olympic_metabolic_check", "OlympicLifts",
     {"metabolic_emphasis": 0.6}, "metabolic", 65.0, ">"),
    (ci.universal_running_zone2_majority, "universal_running_zone2_majority", "Running",
     _HIGH, "metabolic", 50.0, ">"),
    (ci.universal_sprint_neural_freshness, "universal_sprint_neural_freshness", "Sprinting",
     {}, "cns", 58.0, ">"),
    (ci.universal_metcon_fatigue_stack, "universal_metcon_fatigue_stack", "MetCon", {},
     "metabolic", 70.0, ">"),
    (ci.universal_pl_deadlift_cns, "universal_pl_deadlift_cns", "Powerlifting", _HIGH, "cns",
     55.0, ">"),
    (ci.olift_require_freshness_for_heavy_technical_singles,
     "olift_require_freshness_for_heavy_technical_singles", "OlympicLifts",
     {"tags": ["heavy_technical"]}, "cns", 58.0, ">"),
    (ci.prefer_threshold_before_vo2_if_base_low, "prefer_threshold_before_vo2_if_base_low",
     "Running", {"tags": ["vo2_candidate"]}, "aerobic", 25.0, "<"),
    (ci.pl_do_not_max_when_readiness_low, "pl_do_not_max_when_readiness_low", "Powerlifting",
     _HIGH, "cns+metabolic", 62.0, ">"),
    (ci.pl_limit_volume_if_bar_speed_collapse, "pl_limit_volume_if_bar_speed_collapse",
     "Powerlifting", {"volume_load_proxy": 0.8}, "muscular", 60.0, ">"),
]


def _probe_values(threshold: float, direction: str) -> list[tuple[float, bool]]:
    below, at, above = round(threshold - 0.01, 2), threshold, round(threshold + 0.01, 2)
    if direction == ">":
        return [(below, False), (at, False), (above, True)]
    return [(below, True), (at, False), (above, False)]


@pytest.mark.parametrize(("rule", "rule_id", "goal", "cand", "axis", "threshold", "direction"),
                         _CASES, ids=[c[1] for c in _CASES])
def test_constraint_follows_the_canonical_axis(
    rule: Any, rule_id: str, goal: str, cand: dict[str, Any], axis: str, threshold: float,
    direction: str,
) -> None:
    for value, fires in _probe_values(threshold, direction):
        over = ({"cns": value, "metabolic": value} if axis == "cns+metabolic"
                else {axis: value})
        base = _canonical(**over)
        if axis == "aerobic":
            variants = [base, base.model_copy(update={"c_met_aerobic": 500.0 if fires else 0.0})]
        else:
            variants = _variants(base, fires)
        for s in variants:
            res = rule(cand, build_constraint_context(s, [], goal))  # type: ignore[arg-type]
            assert res.code == rule_id
            assert (not res.passed) is fires, (rule_id, value, s.f_met_systemic, s.f_nm_central)


def test_the_constraint_context_carries_no_legacy_scalar() -> None:
    ctx = build_constraint_context(_canonical(metabolic=90.0), [], "Strength")  # type: ignore[arg-type]
    mirrors = {"c_met_aerobic", "c_nm_force", "c_struct", "b_met_anaerobic", "f_met_systemic",
               "f_nm_peripheral", "f_nm_central", "f_struct_damage"}
    assert not mirrors & (set(ctx.athlete_state) | set(ctx.legacy))
    assert ctx.legacy == {"s_struct_signal": 0.0}


# ── the mirror cannot move any decision: random scrambles ──────────────────────

def test_scrambling_the_mirror_changes_no_decision() -> None:
    rng = random.Random(20261021)
    for _ in range(300):
        s = _canonical(cns=rng.uniform(0, 100), muscular=rng.uniform(0, 100),
                       metabolic=rng.uniform(0, 100), aerobic=rng.choice([0.0, rng.uniform(0, 60)]))
        scrambled = s.model_copy(update={
            "f_met_systemic": rng.uniform(0, 100), "f_nm_central": rng.uniform(0, 100),
            "f_nm_peripheral": rng.uniform(0, 100), "f_struct_damage": rng.uniform(0, 100),
            "c_met_aerobic": rng.uniform(0, 600), "c_nm_force": rng.uniform(0, 3000),
            "c_struct": rng.uniform(1, 300), "b_met_anaerobic": rng.uniform(0, 40000),
        })
        for goal in ("Strength", "Power"):
            assert ([c.branch_id for c in _readiness_redirect(s, goal, {})]
                    == [c.branch_id for c in _readiness_redirect(scrambled, goal, {})])
        assert ([c.branch_id for c in _safety_candidates(s)]
                == [c.branch_id for c in _safety_candidates(scrambled)])
        assert _derive_state_evidence(s) == _derive_state_evidence(scrambled)
        for rule, _rid, goal, cand, *_ in _CASES:
            a = rule(cand, build_constraint_context(s, [], goal))  # type: ignore[arg-type]
            b = rule(cand, build_constraint_context(scrambled, [], goal))  # type: ignore[arg-type]
            assert a.passed == b.passed


# ── personalization shadow: the historical mean fatigue ────────────────────────

def _row(payload: Any, **columns: float) -> SimpleNamespace:
    cols = {"f_met_systemic": 0.0, "f_nm_peripheral": 0.0, "f_nm_central": 0.0,
            "f_struct_damage": 0.0}
    cols.update(columns)
    return SimpleNamespace(timestamp=_T, engine_state=payload, **cols)


def _payload(**fatigue: float) -> dict[str, Any]:
    p = default_engine_state_dict()
    p["f"].update(fatigue)
    p["t"]["knee"] = 40.0  # mean tissue over 8 joints = 5.0
    return p


def test_mean_fatigue_is_computed_from_canonical_state_with_the_exact_blend() -> None:
    payload = _payload(cns=10.0, muscular=20.0, metabolic=30.0, structural=5.0, tendon=4.0,
                       grip=10.0)
    # Hand-computed: f_struct_damage = min(100, 5 + 4 + 0.15*10 + 0.1*5.0) = 11.0;
    # mean = (metabolic 30 + muscular 20 + cns 10 + 11.0) / 4 = 17.75.
    consistent = _row(payload, f_met_systemic=30.0, f_nm_peripheral=20.0, f_nm_central=10.0,
                      f_struct_damage=11.0)
    inconsistent = _row(payload, f_met_systemic=99.0, f_nm_peripheral=99.0, f_nm_central=99.0,
                        f_struct_damage=99.0)
    assert ps._mean_fatigue(consistent) == pytest.approx(17.75)
    assert ps._mean_fatigue(inconsistent) == pytest.approx(17.75)


def test_mean_fatigue_struct_blend_clamps_at_100() -> None:
    payload = _payload(structural=90.0, tendon=30.0)
    # min(100, 90 + 30 + 0 + 0.5) = 100; mean = (0 + 0 + 0 + 100) / 4 = 25.
    assert ps._mean_fatigue(_row(payload)) == pytest.approx(25.0)


@pytest.mark.parametrize(
    "payload",
    [None, {"version": 2, "x": {}, "f": {}, "t": {}}, {"version": 99, "x": {}, "f": {}, "t": {}},
     "not json"],
    ids=["null", "empty-vectors", "future-version", "unparseable"],
)
def test_a_row_that_does_not_decode_is_skipped_not_reconstructed(payload: Any) -> None:
    # Populated, plausible legacy columns: a reconstruction would happily use them.
    assert ps._mean_fatigue(_row(payload, f_met_systemic=40.0, f_nm_peripheral=40.0,
                                 f_nm_central=40.0, f_struct_damage=40.0)) is None


@pytest.mark.asyncio
async def test_a_day_whose_latest_row_is_damaged_drops_out_of_the_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date, timedelta

    d0 = date(2026, 10, 1)

    def at(day: int, hour: int) -> datetime:
        return datetime(2026, 10, 1 + day, hour)

    good = _payload(metabolic=40.0)  # mean = 40/4 = 10.0 (tissue blend: 0.1*5 = 0.5 → 10.125)
    better = _payload(metabolic=20.0)
    states = [
        SimpleNamespace(timestamp=at(0, 8), engine_state=good, f_met_systemic=0.0,
                        f_nm_peripheral=0.0, f_nm_central=0.0, f_struct_damage=0.0),
        SimpleNamespace(timestamp=at(1, 8), engine_state=better, f_met_systemic=0.0,
                        f_nm_peripheral=0.0, f_nm_central=0.0, f_struct_damage=0.0),
        # Day 2: a good morning row, then a damaged evening row (the day's latest).
        SimpleNamespace(timestamp=at(2, 8), engine_state=good, f_met_systemic=0.0,
                        f_nm_peripheral=0.0, f_nm_central=0.0, f_struct_damage=0.0),
        SimpleNamespace(timestamp=at(2, 20), engine_state=None, f_met_systemic=50.0,
                        f_nm_peripheral=50.0, f_nm_central=50.0, f_struct_damage=50.0),
        SimpleNamespace(timestamp=at(3, 8), engine_state=better, f_met_systemic=0.0,
                        f_nm_peripheral=0.0, f_nm_central=0.0, f_struct_damage=0.0),
    ]
    wellness = [SimpleNamespace(recorded_date=d0 + timedelta(days=i), sleep_hours=7.0,
                                hrv_ms=60.0, resting_hr=55.0) for i in range(4)]

    class _Repo:
        def __init__(self, _db: object) -> None: ...
        async def list_wellness_history(self, _uid: int) -> list[SimpleNamespace]:
            return wellness
        async def list_states_ascending(self, _uid: int) -> list[SimpleNamespace]:
            return states

    monkeypatch.setattr(ps, "AthleteContextRepository", _Repo)
    _z, y = await ps._build_recovery_frame(object(), 1, clip=3.0)  # type: ignore[arg-type]
    # Pairs: (0→1) and (1→2) and (2→3). Day 2 is unknown, so only (0→1) survives.
    # Hand-computed means (tissue blend 0.1*5.0 = 0.5 in f_struct_damage):
    #   good   = (40 + 0 + 0 + 0.5) / 4 = 10.125;  better = (20 + 0.5) / 4 = 5.125.
    assert list(y) == pytest.approx([10.125 - 5.125])
