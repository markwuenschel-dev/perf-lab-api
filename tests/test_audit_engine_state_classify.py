"""GATE 1 audit — `classify()` maps codec outcomes to the right operational class.

This is the part of the audit that would silently misreport production. `decode_engine_state`
itself is pinned by tests/test_engine_state_codec.py; what is unproven is the THIN mapping
layer on top — codec error_code -> audit classification -> BLOCKING / MIGRATION / DEPLOYMENT.

That mapping is what decides whether the audit tells you "deploy" or "you will break N live
athletes." Getting it wrong in the safe direction wastes a day; getting it wrong in the
other direction ships strict loading into a population it refuses. No DB needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.engine.state_bridge import default_engine_state_dict
from app.scripts.audit_engine_state import BLOCKING, DEPLOYMENT, MIGRATION, classify

TS = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _row(engine_state: object, **overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "id": 1,
        "user_id": 42,
        "timestamp": TS,
        "engine_state": engine_state,
        "c_met_aerobic": 50.0,
        "c_nm_force": 1200.0,
        "c_struct": 60.0,
        "b_met_anaerobic": 9000.0,
        "f_met_systemic": 10.0,
        "f_nm_peripheral": 12.0,
        "f_nm_central": 8.0,
        "f_struct_damage": 15.0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_healthy_row_is_valid_current() -> None:
    assert classify(_row(default_engine_state_dict())).classification == "valid_current"


def test_empty_vectors_classify_as_blocking() -> None:
    """The maxed-athlete defect. If this is ever not BLOCKING, the audit green-lights a
    deploy into rows the prescriber reads as an athlete at maximum strength."""
    v = classify(_row({"version": 2, "x": {}, "f": {}, "t": {}}))
    assert v.classification == "empty_vectors"
    assert v.classification in BLOCKING


def test_partial_vectors_classify_as_blocking() -> None:
    v = classify(_row({"version": 2, "x": {"aerobic": 50.0}}))
    assert v.classification == "partial_vectors"
    assert v.classification in BLOCKING


def test_malformed_classifies_as_blocking() -> None:
    v = classify(_row({"version": 2, "x": "nope", "f": {}, "t": {}}))
    assert v.classification in BLOCKING


def test_null_engine_state_is_migration_not_corruption() -> None:
    """A legacy row that bootstraps from scalars today. Backfill, don't repair — and it must
    NOT be lumped in with damaged rows, because the operational response differs."""
    v = classify(_row(None))
    assert v.classification == "null_engine_state_legacy_row"
    assert v.classification in MIGRATION
    assert v.classification not in BLOCKING


def test_future_version_is_a_deployment_fault_not_corruption() -> None:
    """Never repaired. It means a newer writer is live against older readers."""
    v = classify(_row(default_engine_state_dict() | {"version": 99}))
    assert v.classification == "unsupported_future_version"
    assert v.classification in DEPLOYMENT
    assert v.classification not in BLOCKING
    assert v.declared_version == 99


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_nonfinite_legacy_scalars_block(bad: float) -> None:
    """A NaN capacity is not a number to fall back on — `nan > x` is False everywhere."""
    v = classify(_row(default_engine_state_dict(), c_nm_force=bad))
    assert v.classification == "nonfinite_value"
    assert v.classification in BLOCKING


def test_nonfinite_inside_payload_reports_the_specific_cause() -> None:
    """The codec DOES reject a NaN in a vector — pydantic's ge/le constraints fail against
    NaN, so it raises the generic `vector_validation_failed` and would land in
    `malformed_current`.

    That verdict is not wrong (both are BLOCKING), it is just useless to whoever has to fix
    the row: "malformed" says nothing, "there is a NaN in x" says exactly what to do. The
    audit re-checks and reports the specific cause.
    """
    payload = default_engine_state_dict()
    payload["x"]["max_strength"] = float("nan")
    v = classify(_row(payload))
    assert v.classification == "nonfinite_value"
    assert v.classification in BLOCKING
    assert "non-finite" in v.detail


def test_future_version_payload_is_never_sniffed_for_nonfinite() -> None:
    """Ordering guard. A future payload must not be structurally inspected at all — we do
    not know what its fields mean. Even with a NaN sitting in it, the verdict must stay
    'too new to read', not 'damaged', because the responses differ: deploy readers vs.
    repair the row.
    """
    payload = default_engine_state_dict() | {"version": 99}
    payload["x"]["max_strength"] = float("nan")
    v = classify(_row(payload))
    assert v.classification == "unsupported_future_version"
    assert v.classification not in BLOCKING


def test_null_payload_with_unusable_scalars_blocks_rather_than_backfills() -> None:
    """A legacy row whose legacy scalars are themselves broken cannot be backfilled FROM the
    mirror — so it must not be filed as a routine migration row."""
    v = classify(_row(None, c_nm_force=float("nan")))
    assert v.classification == "nonfinite_value"
    assert v.classification in BLOCKING
    assert v.classification not in MIGRATION


def test_raw_payload_never_appears_in_the_verdict() -> None:
    """Raw engine_state is athlete data. The verdict carries a hash, never content."""
    secret = {"version": 2, "x": {}, "f": {}, "t": {}, "athlete_note": "IDENTIFYING"}
    v = classify(_row(secret))
    assert "IDENTIFYING" not in repr(v)
    assert len(v.hash_) == 16


def test_classification_sets_are_disjoint() -> None:
    """A class in two buckets would produce contradictory verdicts in the same run."""
    assert not (BLOCKING & MIGRATION)
    assert not (BLOCKING & DEPLOYMENT)
    assert not (MIGRATION & DEPLOYMENT)
