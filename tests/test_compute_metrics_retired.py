"""POST /compute-metrics is retired: a 410 tombstone for every submission (DB-free).

Its VO2max was miscalculated (docs/audits/2026-10-21-run-vo2-field-test-audit.md). The route
stays in the contract so an outside caller gets a clear retirement response, never a number.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

VALID = {"age": 35, "sex": "male", "time_300m": "0:55", "time_1p5mi": "10:30"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"json": VALID},  # what used to be a 200 with a wrong VO2
        {"json": {**VALID, "time_1p5mi": "0"}},  # used to be a 422
        {"json": {"age": "x"}},  # malformed shape
        {"json": {}},
        {"json": []},
        {"content": b"not json", "headers": {"content-type": "application/json"}},
        {},  # no body at all
    ],
    ids=["valid", "bad-time", "malformed", "empty-object", "array", "unparseable", "no-body"],
)
def test_every_submission_gets_the_retirement_response(kwargs: dict) -> None:
    resp = client.post("/compute-metrics", **kwargs)
    assert resp.status_code == 410, resp.text
    body = resp.json()
    assert set(body) == {"detail", "replacement"}
    assert "retired" in body["detail"]
    assert "run_1p5mi_time_trial" in body["replacement"]
    assert "/v1/benchmarks/vo2-estimates" in body["replacement"]
    # No metrics of any kind leak through the tombstone.
    for key in ("vo2_max", "vo2_category", "zones", "fatigue_percent", "race_pace_sec_per_mile"):
        assert key not in body


def test_the_contract_advertises_only_the_retirement() -> None:
    op = app.openapi()["paths"]["/compute-metrics"]["post"]
    assert op["deprecated"] is True
    assert "requestBody" not in op
    assert set(op["responses"]) == {"410"}
    schemas = app.openapi()["components"]["schemas"]
    assert "MetricsResponse" not in schemas and "MetricsRequest" not in schemas


@pytest.mark.parametrize("path", ["/program/run", "/program/strength"])
def test_the_other_legacy_routes_are_preserved(path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list) and resp.json()
