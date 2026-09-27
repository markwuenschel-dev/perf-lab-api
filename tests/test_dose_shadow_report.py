"""The 8A shadow-log report: pure aggregation, no database.

The report must not make an empty or thin log look like data, must keep fabricated-set rows
out unless asked, and must count what it cannot use rather than silently dropping it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.models.dose_model_shadow import DoseModelShadowLog
from app.scripts.dose_shadow_report import (
    REPORT_COLUMNS,
    format_report,
    summarize,
)


def _row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_id": 1,
        "session_at": datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        "code_version": None,
        "modality": "Strength",
        "planned_domain": None,
        "planned_category": None,
        "duration_minutes": 45.0,
        "reported_sets": 12.0,
        "ratio_v1_v0": 2.0,
        "v1_density_not_modelled": False,
        "v0_volume_used_fabricated_sets": False,
    }
    base.update(over)
    return base


def test_report_columns_exist_on_the_model() -> None:
    # A renamed column must fail here, not as an AttributeError on the box.
    missing = [c for c in REPORT_COLUMNS if not hasattr(DoseModelShadowLog, c)]
    assert missing == []


def test_an_empty_log_reports_no_rows_not_zeros() -> None:
    result = summarize([])
    assert result == {"status": "no_rows", "total_rows": 0}
    assert "no rows" in format_report(result)


def test_fabricated_set_rows_are_excluded_by_default_and_counted() -> None:
    rows = [_row(ratio_v1_v0=1.0), _row(ratio_v1_v0=9.0, v0_volume_used_fabricated_sets=True)]

    result = summarize(rows)
    assert result["fabricated_set_rows"] == 1
    assert result["fabricated_set_rows_excluded"] == 1
    assert result["analysed_rows"] == 1
    assert result["overall"]["max"] == pytest.approx(1.0)

    included = summarize(rows, include_fabricated=True)
    assert included["fabricated_set_rows_excluded"] == 0
    assert included["overall"]["max"] == pytest.approx(9.0)


def test_only_fabricated_rows_is_reported_as_nothing_left_not_as_data() -> None:
    result = summarize([_row(v0_volume_used_fabricated_sets=True)])
    assert result["status"] == "no_rows_after_exclusion"
    assert "no rows left" in format_report(result)


def test_undefined_ratios_are_counted_never_averaged() -> None:
    result = summarize([_row(ratio_v1_v0=None), _row(ratio_v1_v0=3.0)])
    assert result["rows_with_undefined_ratio"] == 1
    assert result["overall"]["n"] == 1
    assert result["overall"]["median"] == pytest.approx(3.0)


def test_buckets_keep_missing_distinct_from_small() -> None:
    rows = [
        _row(duration_minutes=20.0, reported_sets=None),
        _row(duration_minutes=95.0, reported_sets=0.0),
        _row(duration_minutes=60.0, reported_sets=25.0, planned_category="SBD Strength"),
    ]
    groups = summarize(rows)["groups"]
    assert set(groups["duration"]) == {"<30 min", "60-89 min", ">=90 min"}
    assert set(groups["reported_sets"]) == {"not reported", "<10 sets", ">=20 sets"}
    assert set(groups["planned_category"]) == {"unplanned", "SBD Strength"}


def test_percentiles_on_small_n() -> None:
    one = summarize([_row(ratio_v1_v0=1.5)])["overall"]
    assert (one["p10"], one["median"], one["p90"]) == (1.5, 1.5, 1.5)

    five = summarize([_row(ratio_v1_v0=v) for v in (1.0, 2.0, 3.0, 4.0, 5.0)])["overall"]
    assert five["median"] == pytest.approx(3.0)
    assert five["p10"] == pytest.approx(1.4)
    assert five["p90"] == pytest.approx(4.6)
    assert (five["min"], five["max"]) == (1.0, 5.0)


def test_overview_counts_users_range_and_missing_build_identity() -> None:
    rows = [
        _row(user_id=1, session_at=datetime(2026, 9, 20, tzinfo=UTC)),
        _row(user_id=2, session_at=datetime(2026, 9, 22, tzinfo=UTC), code_version="abc1234"),
    ]
    result = summarize(rows)
    assert result["distinct_users"] == 2
    assert result["session_at_first"].startswith("2026-09-20")
    assert result["session_at_last"].startswith("2026-09-22")
    assert result["rows_without_code_version"] == 1


def test_the_report_says_it_authorizes_nothing() -> None:
    assert "authorizes nothing" in format_report(summarize([_row()]))


# --- census -----------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from app.logic.dose_fit_policy import LoggedSession  # noqa: E402
from app.scripts.dose_shadow_report import census, format_census  # noqa: E402


def _history_for(user: int, n: int, *, first_wl: int, version: str = "v1.2", **over: Any):
    t0 = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    sessions, rows = [], []
    for i in range(n):
        wl = first_wl + i
        at = t0 + timedelta(days=2 * i)
        sessions.append(LoggedSession(workout_log_id=wl, user_id=user, at=at, session_rpe=6.0))
        rows.append(
            _row(
                user_id=user,
                workout_log_id=wl,
                session_at=at,
                v1_model_version=version,
                v1_density_basis="sets_per_elapsed_minute",
                **over,
            )
        )
    return rows, sessions


def test_the_production_census_of_2026_09_27_reports_nothing_to_fit() -> None:
    # EC2 on 2026-09-27: one athlete, one workout log, one shadow row, no next session.
    rows, sessions = _history_for(1, 1, first_wl=1, version="v1")
    real = census(rows, sessions, seeded_user_ids=[])["real"]["by_v1_model_version"]["v1"]
    assert real["eligible_rows"] == 1
    assert real["labelled_pairs"] == 0
    assert real["athletes_holdout_capable"] == 0
    assert real["athletes_with_at_least_n_eligible_sessions"]["2"] == 0
    assert real["eligible_rows_by_pair_status"] == {"no_next_session": 1}


def test_seeded_athletes_are_never_pooled_with_real_ones() -> None:
    real_rows, real_s = _history_for(1, 6, first_wl=1)
    seed_rows, seed_s = _history_for(2, 20, first_wl=100)
    out = census([*real_rows, *seed_rows], [*real_s, *seed_s], seeded_user_ids=[2])
    assert out["real"]["athletes"] == 1 and out["seeded"]["athletes"] == 1
    assert out["real"]["by_v1_model_version"]["v1.2"]["labelled_pairs"] == 6 - 3 - 1
    assert out["seeded"]["by_v1_model_version"]["v1.2"]["athletes_holdout_capable"] == 1
    assert out["real"]["by_v1_model_version"]["v1.2"]["athletes_holdout_capable"] == 0


def test_model_versions_are_counted_separately() -> None:
    a, sa = _history_for(1, 5, first_wl=1, version="v1.1")
    b, sb = _history_for(1, 5, first_wl=50, version="v1.2")
    versions = census([*a, *b], [*sa, *sb], seeded_user_ids=[])["real"]["by_v1_model_version"]
    assert set(versions) == {"v1.1", "v1.2"}
    assert versions["v1.1"]["rows"] == versions["v1.2"]["rows"] == 5


def test_ineligible_rows_are_tiered_and_never_paired() -> None:
    rows, sessions = _history_for(1, 6, first_wl=1)
    rows[4]["v1_density_basis"] = "prescribed_timed_work_over_elapsed"
    rows[3]["v0_volume_used_fabricated_sets"] = True
    v = census(rows, sessions, seeded_user_ids=[])["real"]["by_v1_model_version"]["v1.2"]
    assert v["rows_by_fit_tier"] == {"eligible": 4, "fabricated_sets": 1, "prescribed_proxy": 1}
    # Rows 4 and 5 (wl 4, 5) would have been labelled; only eligible rows count as pairs.
    assert v["labelled_pairs"] == 0
    assert sum(v["eligible_rows_by_pair_status"].values()) == 4


def test_a_row_without_a_workout_log_is_counted_as_unlinked() -> None:
    rows, sessions = _history_for(1, 1, first_wl=1)
    rows[0]["workout_log_id"] = None
    v = census(rows, sessions, seeded_user_ids=[])["real"]["by_v1_model_version"]["v1.2"]
    assert v["eligible_rows_by_pair_status"] == {"unlinked_workout_log": 1}


def test_census_text_names_the_policy_and_the_causal_baseline() -> None:
    rows, sessions = _history_for(1, 2, first_wl=1)
    text = format_census(census(rows, sessions, seeded_user_ids=[]))
    assert "fit policy fit-policy-" in text
    assert "causal baseline" in text
