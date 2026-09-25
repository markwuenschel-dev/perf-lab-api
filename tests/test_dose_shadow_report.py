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
