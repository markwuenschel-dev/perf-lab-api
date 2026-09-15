"""Equipment preference end to end: PATCH /v1/profile → GET /v1/next-session (S-C).

Traverses the real seam — profile write, service context, prescriber, resolver, serialized
explanation — rather than calling the resolver directly (see the "test the seam" rule). What it
pins:

* the preference reaches selection, and the explanation reports its MEASURED effect: the
  "changed N" count equals how many exercises actually differ from the no-preference session;
* the entry is labelled for the athlete in ``constraint_details``;
* a preference never admits an exercise the athlete's equipment rules out.

Requires a live PostgreSQL instance.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

ACCESSORY_NOTE = "Accessory — autoregulate by RPE"


async def _athlete(client: AsyncClient, email: str) -> dict[str, str]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    onboard = await client.post(
        "/v1/onboard",
        json={"experience_level": "intermediate", "available_days_per_week": 4, "goal": "Hypertrophy"},
        headers=headers,
    )
    assert onboard.status_code == 200, onboard.text
    return headers


async def _session(client: AsyncClient, headers: dict[str, str]) -> dict:
    resp = await client.get("/v1/next-session", params={"goal": "Hypertrophy"}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _preference_entries(rx: dict) -> list[dict]:
    return [d for d in rx["why"]["constraint_details"] if d["code"].startswith("equipment:preference=")]


async def test_the_preference_reaches_selection_and_reports_its_measured_effect(
    http_client: AsyncClient, seeded_exercise_catalog
):
    headers = await _athlete(http_client, "equipment-pref-route@test.com")

    plain = await _session(http_client, headers)
    assert _preference_entries(plain) == []

    patch = await http_client.patch("/v1/profile", json={"equipment_preference": ["dumbbell"]}, headers=headers)
    assert patch.status_code == 200, patch.text
    preferred = await _session(http_client, headers)

    plain_names = [e["name"] for e in plain["exercises"] if e["load_note"] != ACCESSORY_NOTE]
    preferred_names = [e["name"] for e in preferred["exercises"] if e["load_note"] != ACCESSORY_NOTE]
    assert plain["type"] == preferred["type"], "the preference must not change which session was chosen"
    differing = sum(1 for a, b in zip(plain_names, preferred_names, strict=True) if a != b)

    (entry,) = _preference_entries(preferred)
    assert entry["code"] == f"equipment:preference=dumbbell(changed={differing})"
    assert entry["athlete_visible"] is True and entry["group"] == "equipment"
    assert entry["label"].startswith("Preference: dumbbells.")


async def test_a_preference_never_admits_what_the_equipment_rules_out(
    http_client: AsyncClient, seeded_exercise_catalog
):
    headers = await _athlete(http_client, "equipment-pref-bounds@test.com")
    patch = await http_client.patch(
        "/v1/profile",
        json={"equipment": ["barbell"], "equipment_preference": ["dumbbell", "machine"]},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text

    rx = await _session(http_client, headers)
    catalog = await http_client.get("/v1/exercises", headers=headers)
    assert catalog.status_code == 200, catalog.text
    needs = {
        row["name"]: {e for e in (row.get("equipment_required") or []) if e not in ("bodyweight", "none", "")}
        for row in catalog.json()
    }
    for exercise in rx["exercises"]:
        assert needs.get(exercise["name"], set()) <= {"barbell"}, exercise["name"]
