"""Oura adapter normalization + per-day dedup (pure; no network)."""
from datetime import date

from app.integrations.oura import OuraAdapter, _sleep_doc_to_wellness


def test_sleep_doc_maps_to_canonical_vocab():
    doc = {
        "day": "2026-07-01",
        "average_hrv": 65,
        "total_sleep_duration": 27000,  # seconds → 7.5 h
        "efficiency": 88,  # already 0–100
        "lowest_heart_rate": 48,
        "some_extra_field": "kept-in-raw",
    }
    readiness = {"day": "2026-07-01", "score": 82}
    w = _sleep_doc_to_wellness("2026-07-01", doc, readiness)

    assert w.day == date(2026, 7, 1)
    assert w.hrv_ms == 65.0
    assert w.sleep_hours == 7.5
    assert w.sleep_quality == 88.0
    assert w.resting_hr == 48.0
    # Oura measures neither of these:
    assert w.soreness is None
    assert w.mood is None
    # Full payloads preserved for provenance:
    assert w.raw["sleep"] is doc
    assert w.raw["daily_readiness"]["score"] == 82


def test_efficiency_ratio_is_rescaled_to_0_100():
    # Defensive: if a 0–1 efficiency ever appears, it is rescaled, not passed raw.
    doc = {"day": "2026-07-02", "efficiency": 0.9, "total_sleep_duration": 3600}
    w = _sleep_doc_to_wellness("2026-07-02", doc, None)
    assert w.sleep_quality == 90.0


def test_missing_fields_become_none():
    w = _sleep_doc_to_wellness("2026-07-03", {"day": "2026-07-03"}, None)
    assert w.hrv_ms is None
    assert w.sleep_hours is None
    assert w.sleep_quality is None
    assert w.resting_hr is None


async def test_fetch_keeps_longest_sleep_per_day(monkeypatch):
    adapter = OuraAdapter(client_id="x", client_secret="y", redirect_uri="http://cb")

    async def fake_get(access_token, endpoint, start, end):
        if endpoint == "sleep":
            return [
                {"day": "2026-07-01", "total_sleep_duration": 3600, "average_hrv": 40},  # nap
                {"day": "2026-07-01", "total_sleep_duration": 28800, "average_hrv": 70},  # main
            ]
        return [{"day": "2026-07-01", "score": 90}]

    monkeypatch.setattr(adapter, "_get_collection", fake_get)
    out = await adapter.fetch_daily_wellness("tok", date(2026, 7, 1), date(2026, 7, 1))

    assert len(out) == 1  # one row per day
    assert out[0].hrv_ms == 70.0  # kept the longer sleep document
    assert out[0].raw["daily_readiness"]["score"] == 90
