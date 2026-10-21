"""B2 — the George 1993 1.5 mi VO₂max estimate, its gate, its population flag, and the policy
that keeps it display-only (pure: no database).

Expected values are worked by hand from Table 2's 1.5-mile model,
VO₂max = 88.02 + 3.716·sex − 0.1656·mass_kg − 2.767·time_min, with 1 lb = 0.45359237 kg:

* male, 163 lb, 12:00 — 163 lb = 73.93555631 kg; 0.1656 × 73.93555631 = 12.243728124936;
  2.767 × 12 = 33.204; 88.02 + 3.716 − 12.243728124936 − 33.204 = 46.288271875064.
* female, 60 kg, 13:30 — 0.1656 × 60 = 9.936; 2.767 × 13.5 = 37.3545;
  88.02 − 9.936 − 37.3545 = 40.7295.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.logic import observation_authority as oa
from app.logic import strength_evidence as se
from app.logic import vo2_estimate as vo2
from app.scripts import seed_benchmarks

MALE_163LB_1200 = 46.288271875064
FEMALE_60KG_1330 = 40.7295


@pytest.fixture
def verified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vo2, "GEORGE_1993_COEFFICIENTS_VERIFIED", True)


@pytest.fixture
def unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vo2, "GEORGE_1993_COEFFICIENTS_VERIFIED", False)


# --- the model and its units ------------------------------------------------------------------


def test_the_coefficients_are_table_2s_and_the_units_are_explicit() -> None:
    assert vo2.COEFFICIENTS == vo2.George1993Coefficients(
        intercept=88.02, sex=3.716, body_mass_kg=-0.1656, elapsed_time_min=-2.767
    )
    assert vo2.LB_TO_KG == 0.45359237
    assert vo2.SEX_MODEL_INPUT == {"male": 1, "female": 0}
    assert vo2.UNITS == "ml/kg/min"


def test_pounds_convert_exactly_and_kilograms_pass_through() -> None:
    assert vo2.body_mass_kg(163, "lb") == pytest.approx(73.93555631, abs=1e-12)
    assert vo2.body_mass_kg(1, "lb") == 0.45359237
    assert vo2.body_mass_kg(73.9, "kg") == 73.9
    with pytest.raises(ValueError):
        vo2.body_mass_kg(70, "stone")


def test_explicit_inputs_give_the_hand_computed_values() -> None:
    assert vo2.vo2max(sex_model_input=1, body_mass_kg=163 * 0.45359237, elapsed_time_min=12.0) == (
        pytest.approx(MALE_163LB_1200, abs=1e-9)
    )
    assert vo2.vo2max(sex_model_input=0, body_mass_kg=60.0, elapsed_time_min=13.5) == (
        pytest.approx(FEMALE_60KG_1330, abs=1e-9)
    )
    with pytest.raises(ValueError):
        vo2.vo2max(sex_model_input=2, body_mass_kg=60.0, elapsed_time_min=13.5)


@pytest.mark.usefixtures("verified")
def test_assess_converts_lb_and_records_every_input_explicitly() -> None:
    meta = vo2.assess(
        elapsed_time_s=720, body_mass_value=163, body_mass_unit="lb", sex="male",
        age_at_performance=26, performed_on=date(2026, 10, 1),
    )
    assert meta["outcome_at_write"]["status"] == "estimated"
    assert meta["outcome_at_write"]["vo2max"] == pytest.approx(MALE_163LB_1200, abs=1e-9)
    assert meta["outcome_at_write"]["units"] == "ml/kg/min"
    assert meta["inputs"] == {
        "elapsed_time_s": 720.0,
        "body_mass_kg": pytest.approx(73.93555631, abs=1e-12),
        "body_mass_entered": {"value": 163.0, "unit": "lb"},
        "sex_model_input": 1,
        "age_at_performance_years": 26,
        "performed_on": "2026-10-01",
    }
    assert meta["estimate_model"]["id"] == "george1993_1p5mi_run"
    assert meta["estimate_model"]["version"] == 1
    assert meta["protocol"] == {"code": "run_1p5mi_time_trial", "version": 1}
    assert meta["uncertainty"]["see"] == 3.9 and meta["uncertainty"]["units"] == "ml/kg/min"
    assert meta["authority"] == "display_only"


@pytest.mark.usefixtures("verified")
def test_the_same_mass_in_kg_and_lb_gives_the_same_estimate() -> None:
    in_lb = vo2.assess(elapsed_time_s=810, body_mass_value=132.27735731, body_mass_unit="lb",
                       sex="female")
    in_kg = vo2.assess(elapsed_time_s=810, body_mass_value=60.0, body_mass_unit="kg",
                       sex="female")
    assert in_kg["outcome_at_write"]["vo2max"] == pytest.approx(FEMALE_60KG_1330, abs=1e-9)
    assert in_lb["outcome_at_write"]["vo2max"] == pytest.approx(FEMALE_60KG_1330, abs=1e-6)


# --- missing inputs, and the verification gate ---------------------------------------------------


@pytest.mark.usefixtures("verified")
def test_missing_inputs_save_the_time_and_give_no_estimate() -> None:
    meta = vo2.assess(elapsed_time_s=744)
    assert meta["inputs"]["elapsed_time_s"] == 744.0
    assert meta["outcome_at_write"] == {"status": "not_estimated", "reason": "missing_inputs",
                               "missing_inputs": ["body_mass", "sex"], "vo2max": None,
                               "units": "ml/kg/min"}
    only_mass = vo2.assess(elapsed_time_s=744, body_mass_value=70, body_mass_unit="kg")
    assert only_mass["outcome_at_write"]["missing_inputs"] == ["sex"]


@pytest.mark.usefixtures("unverified")
def test_a_closed_gate_computes_and_shows_nothing() -> None:
    meta = vo2.assess(elapsed_time_s=720, body_mass_value=163, body_mass_unit="lb", sex="male",
                      age_at_performance=26)
    assert meta["outcome_at_write"]["status"] == "not_estimated"
    assert meta["outcome_at_write"]["reason"] == "coefficients_unverified"
    assert meta["outcome_at_write"]["vo2max"] is None
    assert meta["inputs"]["body_mass_kg"] is not None  # the inputs are still captured
    view = vo2.estimate_view(meta)
    assert view.vo2max is None and view.reason == "coefficients_unverified"


def test_a_saved_estimate_is_hidden_when_the_gate_closes_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vo2, "GEORGE_1993_COEFFICIENTS_VERIFIED", True)
    meta = vo2.assess(elapsed_time_s=720, body_mass_value=163, body_mass_unit="lb", sex="male")
    assert vo2.estimate_view(meta).vo2max == pytest.approx(MALE_163LB_1200, abs=1e-9)
    monkeypatch.setattr(vo2, "GEORGE_1993_COEFFICIENTS_VERIFIED", False)
    assert vo2.estimate_view(meta).vo2max is None


def test_times_the_protocol_cannot_hold_are_refused() -> None:
    for bad in (179.9, 14400.1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            vo2.assess(elapsed_time_s=bad)
    with pytest.raises(ValueError):
        vo2.assess(elapsed_time_s=720, body_mass_value=70)  # a mass with no unit


# --- population -----------------------------------------------------------------------------------


def test_age_at_performance_counts_completed_years() -> None:
    dob = date(2000, 5, 1)
    assert vo2.age_in_full_years(dob, date(2018, 4, 30)) == 17
    assert vo2.age_in_full_years(dob, date(2018, 5, 1)) == 18
    assert vo2.age_in_full_years(dob, date(2030, 4, 30)) == 29
    assert vo2.age_in_full_years(dob, date(2030, 5, 1)) == 30


@pytest.mark.parametrize(
    ("age", "status", "extrapolated"),
    [
        (17, "outside_study_age_range", True),
        (18, "within_study_age_range", False),
        (29, "within_study_age_range", False),
        (30, "outside_study_age_range", True),
        (None, "age_unknown", True),
    ],
)
@pytest.mark.usefixtures("verified")
def test_the_population_flag(age: int | None, status: str, extrapolated: bool) -> None:
    meta = vo2.assess(elapsed_time_s=720, body_mass_value=70, body_mass_unit="kg", sex="male",
                      age_at_performance=age)
    assert meta["population"]["status"] == status
    assert meta["population"]["extrapolated"] is extrapolated
    view = vo2.estimate_view(meta)
    assert view.extrapolated is extrapolated
    if extrapolated:
        assert view.population_label.startswith("Extrapolated and unvalidated")
    else:
        # In range is never called validated for the individual.
        assert view.population_label == "Within the study's age range (18–29)"
        assert "valid" not in view.population_label.lower()


# --- the estimate is rebuilt from the captured inputs ---------------------------------------------


@pytest.mark.usefixtures("verified")
def test_the_view_recomputes_from_the_captured_inputs_only() -> None:
    meta = vo2.assess(elapsed_time_s=720, body_mass_value=163, body_mass_unit="lb", sex="male",
                      age_at_performance=26)
    view = vo2.estimate_view(meta)
    assert view.vo2max == pytest.approx(meta["outcome_at_write"]["vo2max"], abs=1e-12)
    # The captured kg mass is what counts, not the entered figure: edit the entered value and
    # nothing moves; edit the captured kg and the recomputation follows it.
    meta["inputs"]["body_mass_entered"] = {"value": 999.0, "unit": "lb"}
    assert vo2.estimate_view(meta).vo2max == pytest.approx(MALE_163LB_1200, abs=1e-9)
    meta["inputs"]["body_mass_kg"] = 60.0
    assert vo2.estimate_view(meta).vo2max != pytest.approx(MALE_163LB_1200, abs=1e-6)


@pytest.mark.usefixtures("verified")
def test_an_unknown_model_version_is_not_shown() -> None:
    meta = vo2.assess(elapsed_time_s=720, body_mass_value=70, body_mass_unit="kg", sex="male")
    meta["estimate_model"]["version"] = 2
    view = vo2.estimate_view(meta)
    assert view.vo2max is None and view.reason == "unsupported_model"


# --- the authority policy -------------------------------------------------------------------------


def test_the_protocol_code_is_the_one_the_policy_enforces() -> None:
    assert oa.DISPLAY_ONLY_PROTOCOL_CODES == frozenset({vo2.PROTOCOL_CODE})


@pytest.mark.parametrize("mode", [oa.CM_AD_HOC, oa.CM_RETEST, oa.CM_ONBOARDING_ONRAMP])
@pytest.mark.parametrize("requested", [None, oa.CE_BIDIRECTIONAL_UPDATE, oa.CE_INITIALIZE_PRIOR])
def test_display_only_evidence_resolves_to_no_authority(mode: str, requested: str | None) -> None:
    resolution = oa.resolve_authority(
        source_type=oa.ST_ATHLETE_ENTRY, collection_mode=mode,
        evidence_type=se.EV_DISPLAY_ONLY_PERFORMANCE, value_semantics=se.VS_MEASURED,
        protocol_validity=oa.PV_VALID, requested_capacity_effect=requested,
    )
    assert resolution.evidence_cap == oa.CE_NONE
    assert resolution.capacity_effect == oa.CE_NONE
    assert resolution.legacy_flags() == {"affects_capacity": False, "can_regress_capacity": False}


def test_display_only_evidence_is_never_demonstrated_or_capacity_authoritative() -> None:
    assert not oa.is_demonstrated_strength(
        source_type=oa.ST_ATHLETE_ENTRY, evidence_type=se.EV_DISPLAY_ONLY_PERFORMANCE,
        value_semantics=se.VS_MEASURED, protocol_validity=oa.PV_VALID,
    )
    assert se.EV_DISPLAY_ONLY_PERFORMANCE not in se.CAPACITY_AUTHORITATIVE_EVIDENCE


# --- the catalog ----------------------------------------------------------------------------------

_BY_CODE = {row["code"]: row for row in seed_benchmarks.BENCHMARKS}


def test_the_new_definition_has_no_route_to_capacity() -> None:
    row = _BY_CODE[vo2.PROTOCOL_CODE]
    assert (row["metric_type"], row["unit"], row["better_direction"]) == ("time", "seconds", "lower")
    assert row["state_targets"] == []
    assert row.get("standardization_rules") is None
    assert row["observation_weight"] == 0.0
    assert not row["is_primary_anchor"]
    assert vo2.PROTOCOL_CODE not in {m["benchmark_code"] for m in seed_benchmarks.MAPPINGS}


def test_no_derived_metric_reads_a_display_only_definition() -> None:
    import json

    for row in seed_benchmarks.DERIVED_METRICS:
        config = json.dumps(row.get("formula_config") or {})
        for code in oa.DISPLAY_ONLY_PROTOCOL_CODES:
            assert code not in config, row["code"]


def test_the_old_field_test_definition_is_preserved_as_it_was() -> None:
    """Byte-for-byte what origin/main d26e612 seeds for the old code: its row, its mapping and
    its explanation (the explanation pass rewrites the live row's text from this source)."""
    assert _BY_CODE["run_vo2_field_test_300m_1p5mi"] == {
        "code": "run_vo2_field_test_300m_1p5mi", "name": "VO₂ field test (300 m + 1.5 mi)",
        "domain": "running", "metric_type": "score", "unit": "ml_kg_min",
        "is_primary_anchor": True, "is_derived_only": False, "is_validator_only": False,
        "better_direction": "higher", "observation_weight": 0.9,
        "state_targets": ["aerobic"],
        "standardization_rules": {"floor": 25.0, "cap": 70.0},
        "domain_lenses": ["running"],
        "assessable_skill_tags": ["aerobic_capacity"],
        "measurement_protocol": {
            "summary": "Two-part run test: a 300 m all-out and a 1.5 mi time trial.",
            "inputs": ["run_300m_seconds", "run_1p5mi_seconds"],
            "output": "estimated VO₂max (ml/kg/min)",
        },
    }
    assert [m for m in seed_benchmarks.MAPPINGS
            if m["benchmark_code"] == "run_vo2_field_test_300m_1p5mi"] == [
        {"benchmark_code": "run_vo2_field_test_300m_1p5mi", "target_vector": "capacity",
         "target_key": "aerobic", "mapping_type": "residual", "coefficient": 0.9,
         "intercept": 0.0, "config": {}},
    ]
    assert seed_benchmarks.BENCHMARK_EXPLANATIONS["run_vo2_field_test_300m_1p5mi"] == {
        "description": "An estimate of your VO₂max (aerobic capacity). The onramp aerobic "
                       "benchmark.",
        "protocol_summary": "A two-part run test: a 300 m all-out run and a 1.5 mi time trial.",
    }
    assert "run_vo2_field_test_300m_1p5mi" not in seed_benchmarks.SKILL_VIEW_METADATA
