"""Which strength evidence may size a prescribed load, as of a moment (S2) — no database.

Pins the selection contract: eligibility precedes freshness; freshness is measured from
PERFORMANCE time with an exact 28-day boundary; undated and future-dated evidence is
ineligible; timezones are equivalent; the highest qualified value wins with deterministic
ties; expiry can reveal a lower eligible value; the selected observation keeps its own
provenance; and an empty result says why.
"""
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.logic.prescription_evidence import (
    EXPLAIN_ESTIMATE_NOT_USED,
    EXPLAIN_MISSING_DATE,
    EXPLAIN_NO_EVIDENCE,
    EXPLAIN_NOT_QUALIFYING,
    EXPLAIN_SET_NOT_QUALIFYING,
    EXPLAIN_STALE,
    REASON_FUTURE_DATED,
    REASON_INSUFFICIENT_CHARACTERIZATION,
    REASON_MISSING_PERFORMED_AT,
    REASON_NO_EVIDENCE,
    REASON_NOT_PERMITTED,
    REASON_STALE,
    REASON_UNKNOWN_PROVENANCE,
    STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS,
    EvidenceRow,
    explain_missing_basis,
    select_basis,
)

AS_OF = datetime(2026, 9, 14, 12, 0, 0)  # naive UTC, the stored convention
WINDOW = timedelta(days=STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS)


def _entry(observation_id: int = 1, raw: float = 140.0, **over) -> EvidenceRow:
    """A characterized, permitted athlete entry performed yesterday."""
    fields = {
        "observation_id": observation_id,
        "raw_value": raw,
        "performed_at": AS_OF - timedelta(days=1),
        "validity_status": "valid",
        "quarantined_at": None,
        "affects_prescription": True,
        "source_type": "athlete_entry",
        "value_semantics": "measured",
        "source": "benchmark_test",
    }
    fields.update(over)
    return EvidenceRow(**fields)


def _training(observation_id: int = 2, raw: float = 128.0, **over) -> EvidenceRow:
    """A training-derived e1RM from a top set that clears the ADR-0055 gate."""
    fields = {
        "observation_id": observation_id,
        "raw_value": raw,
        "performed_at": AS_OF - timedelta(days=1),
        "validity_status": "valid",
        "quarantined_at": None,
        "affects_prescription": True,
        "source_type": "workout_extraction",
        "value_semantics": "estimated",
        "source": "workout_extraction",
        "reps": 3,
        "load_kg": 120.0,
        "rpe": 9.0,
        "rir": None,
        "effort_fidelity": "set_level",
    }
    fields.update(over)
    return EvidenceRow(**fields)


def test_the_window_is_the_ruled_provisional_28_days() -> None:
    assert STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS == 28


# ── freshness boundary, by performance time ─────────────────────────────────────

def test_evidence_performed_exactly_at_the_window_edge_is_eligible() -> None:
    row = _entry(performed_at=AS_OF - WINDOW)
    assert select_basis([row], as_of=AS_OF).selected == row


def test_one_second_past_the_window_is_stale() -> None:
    result = select_basis([_entry(performed_at=AS_OF - WINDOW - timedelta(seconds=1))], as_of=AS_OF)
    assert result.selected is None
    assert result.reason == REASON_STALE


def test_evidence_performed_at_the_reference_instant_is_eligible() -> None:
    row = _entry(performed_at=AS_OF)
    assert select_basis([row], as_of=AS_OF).selected == row


def test_undated_evidence_is_ineligible() -> None:
    result = select_basis([_entry(performed_at=None)], as_of=AS_OF)
    assert result.selected is None
    assert result.reason == REASON_MISSING_PERFORMED_AT


def test_future_dated_evidence_is_ineligible() -> None:
    result = select_basis([_entry(performed_at=AS_OF + timedelta(seconds=1))], as_of=AS_OF)
    assert result.selected is None
    assert result.reason == REASON_FUTURE_DATED


def test_timezones_are_equivalent() -> None:
    """The same instants, spelled naive-UTC, aware-UTC and aware+05:00, select identically."""
    edge = AS_OF - WINDOW
    plus_five = timezone(timedelta(hours=5))
    spellings = [
        (AS_OF, edge),
        (AS_OF.replace(tzinfo=UTC), edge.replace(tzinfo=UTC)),
        (AS_OF.replace(tzinfo=UTC).astimezone(plus_five), edge.replace(tzinfo=UTC).astimezone(plus_five)),
    ]
    outcomes = []
    for as_of, performed in spellings:
        inside = select_basis([_entry(performed_at=performed)], as_of=as_of)
        outside = select_basis([_entry(performed_at=performed - timedelta(seconds=1))], as_of=as_of)
        outcomes.append((inside.selected is not None, outside.reason))
    assert outcomes == [(True, REASON_STALE)] * 3


# ── selection ────────────────────────────────────────────────────────────────────

def test_the_highest_qualified_value_wins_not_the_latest() -> None:
    heavy_older = _entry(observation_id=1, raw=180.0, performed_at=AS_OF - timedelta(days=27))
    light_recent = _entry(observation_id=2, raw=160.0, performed_at=AS_OF - timedelta(days=1))
    assert select_basis([light_recent, heavy_older], as_of=AS_OF).selected == heavy_older


def test_expiry_reveals_a_lower_eligible_value() -> None:
    """The 180 wins while it is inside the window; once it expires the 160 wins. This is
    the chosen policy, not evidence that capacity fell on the expiry date."""
    heavy = _entry(observation_id=1, raw=180.0, performed_at=AS_OF - timedelta(days=27))
    light = _entry(observation_id=2, raw=160.0, performed_at=AS_OF - timedelta(days=1))
    two_days_later = AS_OF + timedelta(days=2)
    assert select_basis([heavy, light], as_of=two_days_later).selected == light


def test_equal_values_tie_to_the_newest_performance() -> None:
    older = _entry(observation_id=9, raw=150.0, performed_at=AS_OF - timedelta(days=5))
    newer = _entry(observation_id=3, raw=150.0, performed_at=AS_OF - timedelta(days=2))
    assert select_basis([older, newer], as_of=AS_OF).selected == newer


def test_equal_values_and_performance_tie_to_the_higher_observation_id() -> None:
    same = AS_OF - timedelta(days=2)
    low_id = _entry(observation_id=4, raw=150.0, performed_at=same)
    high_id = _entry(observation_id=7, raw=150.0, performed_at=same)
    assert select_basis([high_id, low_id], as_of=AS_OF).selected == high_id
    assert select_basis([low_id, high_id], as_of=AS_OF).selected == high_id


def test_a_selected_estimate_keeps_its_own_provenance() -> None:
    training = _training(raw=190.0)
    selected = select_basis([_entry(raw=140.0), training], as_of=AS_OF).selected
    assert selected is not None
    assert (selected.source_type, selected.value_semantics) == ("workout_extraction", "estimated")


# ── eligibility precedes freshness ───────────────────────────────────────────────

@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"affects_prescription": False}, REASON_NOT_PERMITTED),
        ({"affects_prescription": None}, REASON_NOT_PERMITTED),
        ({"validity_status": "quarantined"}, REASON_NOT_PERMITTED),
        ({"validity_status": "invalid"}, REASON_NOT_PERMITTED),
        ({"quarantined_at": AS_OF - timedelta(days=3)}, REASON_NOT_PERMITTED),
        ({"source_type": "legacy_unknown"}, REASON_UNKNOWN_PROVENANCE),
        ({"source_type": None}, REASON_UNKNOWN_PROVENANCE),
        ({"value_semantics": "unknown"}, REASON_INSUFFICIENT_CHARACTERIZATION),
        ({"value_semantics": None}, REASON_INSUFFICIENT_CHARACTERIZATION),
        ({"raw_value": None}, REASON_INSUFFICIENT_CHARACTERIZATION),
    ],
)
def test_a_recent_date_does_not_rehabilitate_ineligible_evidence(override, reason) -> None:
    fresh_but_ineligible = _entry(observation_id=1, raw=200.0, performed_at=AS_OF, **override)
    lower_eligible = _entry(observation_id=2, raw=130.0)
    assert select_basis([fresh_but_ineligible, lower_eligible], as_of=AS_OF).selected == lower_eligible
    alone = select_basis([fresh_but_ineligible], as_of=AS_OF)
    assert alone.selected is None
    assert alone.reason == reason


@pytest.mark.parametrize(
    "override",
    [
        {"reps": 8},                                              # past the 1-5 rep gate
        {"reps": None},
        {"load_kg": None},
        {"rpe": 7.0, "rir": None},                                # not near failure
        {"effort_fidelity": "group_level", "rpe": 8.5},           # cloned effort needs RPE 9
        {"effort_fidelity": None, "rpe": 8.5},                    # unstated fidelity fails closed
    ],
)
def test_training_evidence_must_still_clear_the_extraction_gate(override) -> None:
    result = select_basis([_training(**override)], as_of=AS_OF)
    assert result.selected is None
    assert result.reason == REASON_INSUFFICIENT_CHARACTERIZATION


def test_group_level_training_evidence_at_rpe_nine_qualifies() -> None:
    row = _training(effort_fidelity="group_level", rpe=9.0)
    assert select_basis([row], as_of=AS_OF).selected == row


# ── why nothing qualified ────────────────────────────────────────────────────────

def test_no_rows_means_no_evidence() -> None:
    assert select_basis([], as_of=AS_OF).reason == REASON_NO_EVIDENCE


def test_the_reason_names_the_row_that_came_closest_to_qualifying() -> None:
    """Stale beats undated beats uncharacterized: each is one step further along."""
    stale = _entry(observation_id=1, performed_at=AS_OF - WINDOW - timedelta(days=1))
    undated = _entry(observation_id=2, performed_at=None)
    uncharacterized = _entry(observation_id=3, value_semantics="unknown")
    assert select_basis([uncharacterized, undated, stale], as_of=AS_OF).reason == REASON_STALE
    assert select_basis([uncharacterized, undated], as_of=AS_OF).reason == REASON_MISSING_PERFORMED_AT
    assert select_basis([uncharacterized], as_of=AS_OF).reason == REASON_INSUFFICIENT_CHARACTERIZATION


def test_a_selection_carries_no_reason() -> None:
    assert select_basis([_entry()], as_of=AS_OF).reason is None


# ── the qualifying-set gate follows the evidence kind, not the entry route ──────

def test_an_athlete_reported_set_faces_the_same_gate_as_training_evidence() -> None:
    """A rep-max entered in Assess is an athlete entry, not workout extraction — but it is
    set-derived evidence all the same, so it must clear the same qualifying-set gate."""
    too_many_reps = _entry(value_semantics="estimated", reps=8, load_kg=120.0, rpe=9.0,
                           effort_fidelity="set_level")
    result = select_basis([too_many_reps], as_of=AS_OF)
    assert result.selected is None
    assert result.reason == REASON_INSUFFICIENT_CHARACTERIZATION

    qualifying = _entry(value_semantics="estimated", reps=3, load_kg=120.0, rpe=9.0,
                        effort_fidelity="set_level")
    assert select_basis([qualifying], as_of=AS_OF).selected == qualifying


@pytest.mark.parametrize("semantics", ["estimated", "lower_bound"])
def test_non_measured_evidence_without_a_set_is_not_a_basis(semantics) -> None:
    """An unsupported estimate carries nothing to qualify, however recent or permitted."""
    result = select_basis([_entry(value_semantics=semantics)], as_of=AS_OF)
    assert result.selected is None
    assert result.reason == REASON_INSUFFICIENT_CHARACTERIZATION


def test_a_measured_entry_is_a_direct_performance_and_needs_no_set() -> None:
    tested = _entry(value_semantics="measured")
    assert select_basis([tested], as_of=AS_OF).selected == tested


# ── what explains an empty selection ─────────────────────────────────────────────

def test_the_explanatory_row_is_the_most_recent_row_that_got_furthest() -> None:
    older_stale = _entry(observation_id=1, performed_at=AS_OF - WINDOW - timedelta(days=10))
    newer_stale = _entry(observation_id=2, performed_at=AS_OF - WINDOW - timedelta(days=3))
    undated = _entry(observation_id=3, performed_at=None)
    result = select_basis([older_stale, undated, newer_stale], as_of=AS_OF)
    assert result.reason == REASON_STALE
    assert result.explanatory == newer_stale


def test_among_undated_rows_the_newest_report_explains() -> None:
    first = _entry(observation_id=4, performed_at=None)
    second = _entry(observation_id=9, performed_at=None)
    assert select_basis([second, first], as_of=AS_OF).explanatory == second


def test_a_selection_or_no_evidence_has_no_explanatory_row() -> None:
    assert select_basis([_entry()], as_of=AS_OF).explanatory is None
    assert select_basis([], as_of=AS_OF).explanatory is None


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([_entry(performed_at=AS_OF - WINDOW - timedelta(days=1))], EXPLAIN_STALE),
        ([_entry(performed_at=None)], EXPLAIN_MISSING_DATE),
        ([_entry(evidence_type="reported_estimate", value_semantics="estimated",
                 affects_prescription=False)], EXPLAIN_ESTIMATE_NOT_USED),
        ([_training(reps=8)], EXPLAIN_SET_NOT_QUALIFYING),
        ([_entry(value_semantics="estimated", reps=8, load_kg=120.0, rpe=9.0,
                 effort_fidelity="set_level")], EXPLAIN_SET_NOT_QUALIFYING),
        ([], EXPLAIN_NO_EVIDENCE),
        ([_entry(source_type="legacy_unknown")], EXPLAIN_NOT_QUALIFYING),
        ([_entry(validity_status="quarantined")], EXPLAIN_NOT_QUALIFYING),
        ([_entry(value_semantics="estimated")], EXPLAIN_NOT_QUALIFYING),
    ],
)
def test_why_there_is_no_weight_in_athlete_terms(rows, expected) -> None:
    """Eligibility language, never a verdict that the athlete got weaker."""
    assert explain_missing_basis(select_basis(rows, as_of=AS_OF)) == expected


def test_a_selection_needs_no_explanation() -> None:
    assert explain_missing_basis(select_basis([_entry()], as_of=AS_OF)) is None


# ── comparability ────────────────────────────────────────────────────────────────

def test_each_e1rm_benchmark_code_belongs_to_exactly_one_catalog_exercise() -> None:
    """"Comparable = the same e1RM benchmark code" is accepted for the three canonical lifts
    only. If a variant (a front squat, a paused bench) is ever mapped onto an existing code,
    this fails — so widening coverage (S4) cannot silently start sharing evidence between
    variants that were never shown to be load-comparable."""
    from app.data.exercise_bulk import bulk_exercises
    from app.scripts.seed_exercises import EXERCISES

    by_code: dict[str, list[str]] = {}
    for row in [*EXERCISES, *bulk_exercises()]:
        if row.get("e1rm_benchmark_code"):
            by_code.setdefault(row["e1rm_benchmark_code"], []).append(row["name"])
    assert by_code == {
        "pl_e1rm_squat": ["Back Squat"],
        "pl_e1rm_bench": ["Bench Press"],
        "pl_e1rm_deadlift": ["Conventional Deadlift"],
    }
