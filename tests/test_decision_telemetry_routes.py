"""Route contract tests for prescriber decision telemetry (Workstream B).

Hitting a prescriber entry point (/v1/next-session and /v1/planning/today)
must persist first-party decision labels: one prescription_decisions row plus
one candidate_decision_logs row per considered candidate, with exactly one
candidate flagged chosen. Data-capture only — the prescription response itself
must be unaffected.

Mirrors the http_client + real-auth-flow pattern in
tests/test_objectives_routes.py. Requires a live DB (skips gracefully via the
async_db fixture otherwise).
"""
import pytest
from sqlalchemy import func, select

from app.models.telemetry import CandidateDecisionLog, PrescriptionDecision

pytestmark = pytest.mark.asyncio


async def _register_and_get_token(client, email: str, password: str) -> str:
    reg = await client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


async def test_next_session_persists_decision_and_candidate_logs(http_client, async_db):
    token = await _register_and_get_token(http_client, "telemetry_next@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session", headers=hdr)
    assert resp.status_code == 200, resp.text
    rx = resp.json()
    # The prescription response shape is unchanged (data-capture only).
    assert {"type", "focus", "rationale", "duration_min"} <= set(rx)

    decisions = (
        (await async_db.execute(select(PrescriptionDecision))).scalars().all()
    )
    assert len(decisions) == 1, "exactly one prescription_decisions row per call"
    decision = decisions[0]
    assert decision.decision_mode == "adaptive"
    assert decision.algorithm_version == rx["model_version"]
    assert decision.chosen_candidate_id is not None
    assert decision.chosen_score is not None
    assert decision.state_snapshot_json is not None

    logs = (
        (
            await async_db.execute(
                select(CandidateDecisionLog).where(
                    CandidateDecisionLog.prescription_decision_id == decision.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) >= 1, "the full ranked candidate pool must be logged"
    chosen = [log for log in logs if log.chosen]
    assert len(chosen) == 1, "exactly one candidate is flagged chosen"
    assert chosen[0].branch_id == decision.chosen_candidate_id
    assert chosen[0].final_score is not None
    assert chosen[0].score_components_json is not None
    # The chosen candidate's telemetry describes the returned prescription.
    assert chosen[0].candidate_type == rx["type"]


async def test_next_session_response_matches_without_telemetry(http_client, async_db, monkeypatch):
    """Proves telemetry is non-invasive: neutralising the writer leaves the
    prescription response byte-identical (same seed user, same call)."""
    token = await _register_and_get_token(http_client, "telemetry_noop@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    with_telemetry = (await http_client.get("/v1/next-session", headers=hdr)).json()

    async def _noop(*args, **kwargs):
        return None

    # Patch both entry points' imported reference to the writer.
    monkeypatch.setattr(
        "app.services.prescription_service.persist_prescription_decision", _noop
    )
    without_telemetry = (await http_client.get("/v1/next-session", headers=hdr)).json()

    assert with_telemetry == without_telemetry

    # And with the writer disabled, no new decision rows appear for the 2nd call.
    count = (
        await async_db.execute(select(func.count()).select_from(PrescriptionDecision))
    ).scalar_one()
    assert count == 1
