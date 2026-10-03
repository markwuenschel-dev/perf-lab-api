"""W1-b — POST /compute-metrics rejects times it cannot compute with (DB-free).

The legacy calculator divided by the parsed 1.5-mile time (`fatigue_factor`) with no
guard, so `"0"` — and anything else that parsed to zero or a non-finite number — was an
unauthenticated 500. Malformed input must be a 422 the client can act on.
"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from app.api.v1.legacy import parse_time_to_seconds
from app.main import app

client = TestClient(app)

VALID = {"age": 35, "sex": "male", "time_300m": "0:55", "time_1p5mi": "10:30"}

BAD_TIMES = ["0", "0:00", "00:00:00", "-1", "-0:30", "1:-30", "nan", "inf", "-inf",
             "1e400", "abc", "", "   ", "1:2:3:4", "1::30", ":", "nan:00", "10:inf"]


@pytest.mark.parametrize("field", ["time_300m", "time_1p5mi"])
@pytest.mark.parametrize("bad", BAD_TIMES)
def test_bad_time_is_422_not_500(field: str, bad: str) -> None:
    resp = client.post("/compute-metrics", json={**VALID, field: bad})
    assert resp.status_code == 422, (bad, resp.status_code, resp.text)


@pytest.mark.parametrize(
    ("t300", "t15"),
    [
        # Review regressions: accepted-positive values the formulas cannot use.
        ("0:55", "1e-323"),  # underflows the 1.5 mi pace denominator to 0 → was a 500
        ("1e308", "1"),  # overflows the fatigue ratio → was a 200 with fatigue_percent null
        # Bounds edges just outside the usable ranges.
        ("19.99", "10:30"), ("30:00.01", "10:30"), ("0:55", "179.99"), ("0:55", "4:00:00.01"),
    ],
)
def test_unusable_times_are_422(t300: str, t15: str) -> None:
    resp = client.post("/compute-metrics", json={**VALID, "time_300m": t300, "time_1p5mi": t15})
    assert resp.status_code == 422, (t300, t15, resp.status_code, resp.text)


@pytest.mark.parametrize(("t300", "t15"), [("20", "3:00"), ("30:00", "4:00:00")])
def test_bound_edges_are_accepted_and_finite(t300: str, t15: str) -> None:
    resp = client.post("/compute-metrics", json={**VALID, "time_300m": t300, "time_1p5mi": t15})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    numbers = [body["vo2_max"], body["fatigue_percent"], body["race_pace_sec_per_mile"]] + [
        z[k] for z in body["zones"] for k in ("slow_pace_sec", "fast_pace_sec")
    ]
    assert all(isinstance(n, float | int) and math.isfinite(n) for n in numbers), body


@pytest.mark.parametrize("field", ["time_300m", "time_1p5mi"])
def test_missing_time_is_422(field: str) -> None:
    body = {k: v for k, v in VALID.items() if k != field}
    assert client.post("/compute-metrics", json=body).status_code == 422


def test_valid_input_golden_response() -> None:
    resp = client.post("/compute-metrics", json=VALID)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 1.5 mi in 630 s → 10.5 min; formula 88.02 − 0.1656·163 − 2.76·10.5 + 3.716.
    assert body["vo2_max"] == pytest.approx(88.02 - 0.1656 * 163 - 2.76 * 10.5 + 3.716)
    assert body["race_pace_sec_per_mile"] == pytest.approx(420.0)
    # pace_300 = 55/300, pace_1p5 = 630/2400 → ff − 1 as a percent.
    assert body["fatigue_percent"] == pytest.approx(((55 / 300) / (630 / 2400) - 1) * 100)
    assert body["result_category"] == "Good"
    assert len(body["zones"]) == 5


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("55", 55.0), ("0:55", 55.0), ("10:30", 630.0), ("1:02:03", 3723.0), ("10:30.5", 630.5)],
)
def test_parser_accepts_the_documented_shapes(text: str, seconds: float) -> None:
    assert parse_time_to_seconds(text) == pytest.approx(seconds)
    assert math.isfinite(parse_time_to_seconds(text))
