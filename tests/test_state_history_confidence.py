"""A0: the /v1/state-history confidence-presentation contract.

Locks the endpoint-specific projection ``StateHistorySnapshotRead`` (ADR-0059):
each recorded snapshot carries a per-axis confidence *band* derived from THAT
row's own variance, plus the policy version — so the Twin renders certainty
without re-declaring the thresholds client-side. Canonical ``UnifiedStateVector``
stays free of presentation state.
"""
from datetime import datetime

import pytest

from app.domain.vectors import CapacityConfidence
from app.logic.confidence_presentation import (
    POLICY_VERSION,
    STATUS_ESTABLISHED,
    STATUS_INSUFFICIENT,
    STATUS_PROVISIONAL,
)
from app.schemas.state import StateHistorySnapshotRead, UnifiedStateVector


def _vec(**variances: float) -> UnifiedStateVector:
    """A minimal state vector with the given per-axis capacity variances."""
    return UnifiedStateVector(
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        capacity_confidence=CapacityConfidence(**variances),
        c_met_aerobic=0.0,
        c_nm_force=0.0,
        c_struct=0.0,
        b_met_anaerobic=0.0,
    )


# ── projection / per-row derivation ───────────────────────────────────────────

def test_from_state_derives_band_from_that_rows_own_variance():
    # aerobic <= 0.35 established; glycolytic <= 1.05 provisional; max_strength > 1.05 insufficient
    snap = StateHistorySnapshotRead.from_state(
        _vec(aerobic=0.10, glycolytic=0.50, max_strength=1.20), snapshot_id=7
    )
    status = snap.capacity_confidence_status
    assert status["aerobic"] == STATUS_ESTABLISHED
    assert status["glycolytic"] == STATUS_PROVISIONAL
    assert status["max_strength"] == STATUS_INSUFFICIENT
    assert snap.confidence_presentation_policy_version == POLICY_VERSION
    assert snap.snapshot_id == 7


def test_from_state_covers_all_eight_capacity_axes():
    snap = StateHistorySnapshotRead.from_state(_vec(), snapshot_id=1)
    assert set(snap.capacity_confidence_status) == set(CapacityConfidence.KEYS)
    assert len(snap.capacity_confidence_status) == 8


def test_from_state_preserves_canonical_vector_fields_and_carries_id():
    vec = _vec(power=0.2)
    snap = StateHistorySnapshotRead.from_state(vec, snapshot_id=42)
    assert snap.snapshot_id == 42  # persisted identity survives the projection
    assert snap.timestamp == vec.timestamp
    assert snap.capacity_x == vec.capacity_x
    assert snap.fatigue_f == vec.fatigue_f
    assert snap.tissue_t == vec.tissue_t
    assert snap.capacity_confidence == vec.capacity_confidence


def test_thresholds_are_at_the_boundaries():
    """Boundary values land on the inclusive side of each band (<=)."""
    snap = StateHistorySnapshotRead.from_state(
        _vec(aerobic=0.35, glycolytic=1.05, max_strength=1.06), snapshot_id=1
    )
    assert snap.capacity_confidence_status["aerobic"] == STATUS_ESTABLISHED
    assert snap.capacity_confidence_status["glycolytic"] == STATUS_PROVISIONAL
    assert snap.capacity_confidence_status["max_strength"] == STATUS_INSUFFICIENT


# ── API shape ─────────────────────────────────────────────────────────────────

async def _register_and_login(client, email: str) -> str:
    reg = await client.post("/auth/register", json={"email": email, "password": "testpass99"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "testpass99"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


@pytest.mark.asyncio
async def test_state_history_requires_auth(http_client):
    assert (await http_client.get("/v1/state-history")).status_code == 401


@pytest.mark.asyncio
async def test_state_history_returns_confidence_projection(http_client):
    token = await _register_and_login(http_client, "twin_hist@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    # /v1/next-session auto-inits and commits a baseline S0 state row.
    assert (await http_client.get("/v1/next-session", headers=headers)).status_code == 200

    resp = await http_client.get("/v1/state-history", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) >= 1

    row = rows[-1]
    # canonical vector fields survive the projection
    assert "timestamp" in row and "capacity_x" in row and "capacity_confidence" in row
    # + the persisted identity for cross-screen deep-link / scrub keying
    assert isinstance(row["snapshot_id"], int)
    # + the presentation contract
    assert set(row["capacity_confidence_status"]) == set(CapacityConfidence.KEYS)
    assert row["confidence_presentation_policy_version"] == POLICY_VERSION
    assert all(
        v in {STATUS_ESTABLISHED, STATUS_PROVISIONAL, STATUS_INSUFFICIENT}
        for v in row["capacity_confidence_status"].values()
    )
