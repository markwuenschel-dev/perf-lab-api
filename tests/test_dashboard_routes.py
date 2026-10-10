"""Route contract tests for /v1/dashboard/kpis, /v1/dashboard/domain-summary, /v1/dashboard/readiness."""
from datetime import datetime

import pytest

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.derived_metric_definition import DerivedMetricDefinition
from app.models.derived_metric_snapshot import DerivedMetricSnapshot

pytestmark = pytest.mark.asyncio


async def _register_and_get_token(client, email: str, password: str) -> str:
    """Register a user and return a Bearer token string."""
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": password},
    )
    assert reg.status_code == 201, reg.text

    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


async def test_dashboard_kpis_authenticated(http_client):
    """GET /v1/dashboard/kpis with a valid Bearer token returns 200 with kpis and primary_anchors keys."""
    token = await _register_and_get_token(http_client, "dash_kpis@test.com", "securepass1")

    resp = await http_client.get(
        "/v1/dashboard/kpis",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "kpis" in data
    assert "primary_anchors" in data
    assert isinstance(data["kpis"], list)
    assert isinstance(data["primary_anchors"], list)


async def test_dashboard_domain_summary_authenticated(http_client):
    """GET /v1/dashboard/domain-summary?domain=strength with a valid Bearer token returns 200 with expected keys."""
    token = await _register_and_get_token(http_client, "dash_domain@test.com", "securepass1")

    resp = await http_client.get(
        "/v1/dashboard/domain-summary",
        params={"domain": "strength"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "domain" in data
    assert "kpis" in data
    assert "primary_anchors" in data
    assert isinstance(data["domain"], str)
    assert isinstance(data["kpis"], list)
    assert isinstance(data["primary_anchors"], list)


async def test_dashboard_readiness_authenticated(http_client):
    """GET /v1/dashboard/readiness with a valid Bearer token returns 200 with state and kpi_flags keys."""
    token = await _register_and_get_token(http_client, "dash_readiness@test.com", "securepass1")

    resp = await http_client.get(
        "/v1/dashboard/readiness",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "state" in data
    assert "kpi_flags" in data


async def test_dashboard_unauthenticated(http_client):
    """GET /v1/dashboard/kpis with no Authorization header returns 401."""
    resp = await http_client.get("/v1/dashboard/kpis")
    assert resp.status_code == 401, resp.text


async def test_dashboard_readiness_no_state_not_500(http_client):
    """GET /v1/dashboard/readiness for a fresh user (no AthleteState rows) returns 200 with state=null."""
    token = await _register_and_get_token(http_client, "dash_no_state@test.com", "securepass1")

    resp = await http_client.get(
        "/v1/dashboard/readiness",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"] is None


# ---------------------------------------------------------------------------
# Exact-payload characterization for /kpis and /domain-summary.
#
# The tests above only assert that keys exist on an empty account, so every
# field mapping (and the ordering rules) between the service rows and the
# response models was unpinned. These seed a known fixture and assert the whole
# JSON body, so any renamed/dropped/reordered field is a failure — which is what
# makes the dict->typed-model refactor of dashboard_service verifiable as
# behaviour-preserving rather than merely "still 200".
# ---------------------------------------------------------------------------


async def _user_id(client, headers) -> int:
    me = await client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    return me.json()["id"]


async def _seed_dashboard_fixture(async_db, user_id: int) -> None:
    """Two KPIs (different domains/priorities) + anchors, one non-anchor, one stale obs."""
    kpi_power = DerivedMetricDefinition(
        code="zz_test_power",
        name="Test Power",
        domain="strength",
        metric_type="score",
        unit="pts",
        formula_type="sum",
        formula_config={"benchmark_codes": []},
        display_priority=10,
        is_dashboard_kpi=True,
        can_affect_prescriber_rules=True,
    )
    # Alphabetically first but lower display priority: proves the ordering is by
    # (display_priority, code) and not incidentally alphabetical.
    kpi_engine = DerivedMetricDefinition(
        code="aa_test_engine",
        name="Test Engine",
        domain="endurance",
        metric_type="ratio",
        unit="pct",
        formula_type="sum",
        formula_config={"benchmark_codes": []},
        display_priority=20,
        is_dashboard_kpi=False,
        can_affect_prescriber_rules=False,
    )
    async_db.add_all([kpi_power, kpi_engine])

    anchor_squat = BenchmarkDefinition(
        code="zz_anchor_squat",
        name="Test Squat",
        domain="strength",
        metric_type="load",
        unit="kg",
        is_primary_anchor=True,
        better_direction="higher",
        observation_weight=1.0,
    )
    anchor_row = BenchmarkDefinition(
        code="aa_anchor_row",
        name="Test Row 2k",
        domain="endurance",
        metric_type="time",
        unit="s",
        is_primary_anchor=True,
        better_direction="lower",
        observation_weight=1.0,
    )
    # Not an anchor: must never appear in primary_anchors even with an observation.
    not_anchor = BenchmarkDefinition(
        code="mm_not_anchor",
        name="Test Not Anchor",
        domain="strength",
        metric_type="load",
        unit="kg",
        is_primary_anchor=False,
        better_direction="higher",
        observation_weight=1.0,
    )
    async_db.add_all([anchor_squat, anchor_row, not_anchor])
    await async_db.flush()

    async_db.add_all(
        [
            DerivedMetricSnapshot(
                user_id=user_id,
                derived_metric_definition_id=kpi_power.id,
                computed_at=datetime(2026, 1, 2, 3, 4, 5),
                value=123.5,
                confidence=0.85,
            ),
            DerivedMetricSnapshot(
                user_id=user_id,
                derived_metric_definition_id=kpi_engine.id,
                computed_at=datetime(2026, 1, 3, 4, 5, 6),
                value=77.25,
                confidence=None,
            ),
            BenchmarkObservation(
                user_id=user_id,
                benchmark_definition_id=anchor_squat.id,
                observed_at=datetime(2026, 1, 5, 0, 0, 0),
                raw_value=180.0,
                validity_status="valid",
            ),
            # Older observation for the same anchor: the latest one must win.
            BenchmarkObservation(
                user_id=user_id,
                benchmark_definition_id=anchor_squat.id,
                observed_at=datetime(2025, 12, 1, 0, 0, 0),
                raw_value=150.0,
                validity_status="valid",
            ),
            BenchmarkObservation(
                user_id=user_id,
                benchmark_definition_id=anchor_row.id,
                observed_at=datetime(2026, 1, 4, 0, 0, 0),
                raw_value=420.5,
                validity_status="valid",
            ),
            BenchmarkObservation(
                user_id=user_id,
                benchmark_definition_id=not_anchor.id,
                observed_at=datetime(2026, 1, 6, 0, 0, 0),
                raw_value=99.0,
                validity_status="valid",
            ),
        ]
    )
    await async_db.commit()


_EXPECTED_KPI_POWER = {
    "code": "zz_test_power",
    "name": "Test Power",
    "domain": "strength",
    "metric_type": "score",
    "unit": "pts",
    "value": 123.5,
    "confidence": 0.85,
    "computed_at": "2026-01-02T03:04:05",
    "is_dashboard_kpi": True,
    "can_affect_prescriber_rules": True,
    "value_basis": "unknown",  # a snapshot written without lineage
}
_EXPECTED_KPI_ENGINE = {
    "code": "aa_test_engine",
    "name": "Test Engine",
    "domain": "endurance",
    "metric_type": "ratio",
    "unit": "pct",
    "value": 77.25,
    "confidence": None,
    "computed_at": "2026-01-03T04:05:06",
    "is_dashboard_kpi": False,
    "can_affect_prescriber_rules": False,
    "value_basis": "unknown",
}
_EXPECTED_ANCHOR_ROW = {
    "benchmark_code": "aa_anchor_row",
    "name": "Test Row 2k",
    "domain": "endurance",
    "is_primary_anchor": True,
    "metric_type": "time",
    "unit": "s",
    "raw_value": 420.5,
    "observed_at": "2026-01-04T00:00:00",
    "evidence_type": None,
    "value_semantics": None,
}
_EXPECTED_ANCHOR_SQUAT = {
    "benchmark_code": "zz_anchor_squat",
    "name": "Test Squat",
    "domain": "strength",
    "is_primary_anchor": True,
    "metric_type": "load",
    "unit": "kg",
    "raw_value": 180.0,
    "observed_at": "2026-01-05T00:00:00",
    "evidence_type": None,
    "value_semantics": None,
}


async def test_dashboard_kpis_payload_is_exact(http_client, async_db):
    """GET /v1/dashboard/kpis returns the full mapped payload, field for field."""
    token = await _register_and_get_token(http_client, "dash_kpis_exact@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    await _seed_dashboard_fixture(async_db, await _user_id(http_client, hdr))

    resp = await http_client.get("/v1/dashboard/kpis", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        # Ordered by (display_priority, code).
        "kpis": [_EXPECTED_KPI_POWER, _EXPECTED_KPI_ENGINE],
        # Ordered by (domain, benchmark_code); non-anchor excluded; latest obs wins.
        "primary_anchors": [_EXPECTED_ANCHOR_ROW, _EXPECTED_ANCHOR_SQUAT],
    }


async def test_dashboard_domain_summary_payload_is_exact(http_client, async_db):
    """GET /v1/dashboard/domain-summary filters the same bundle to one domain."""
    token = await _register_and_get_token(http_client, "dash_domain_exact@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    await _seed_dashboard_fixture(async_db, await _user_id(http_client, hdr))

    resp = await http_client.get(
        "/v1/dashboard/domain-summary", params={"domain": "strength"}, headers=hdr
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "domain": "strength",
        "kpis": [_EXPECTED_KPI_POWER],
        "primary_anchors": [_EXPECTED_ANCHOR_SQUAT],
    }

    other = await http_client.get(
        "/v1/dashboard/domain-summary", params={"domain": "endurance"}, headers=hdr
    )
    assert other.status_code == 200, other.text
    assert other.json() == {
        "domain": "endurance",
        "kpis": [_EXPECTED_KPI_ENGINE],
        "primary_anchors": [_EXPECTED_ANCHOR_ROW],
    }


async def test_dashboard_domain_summary_unknown_domain_is_empty(http_client, async_db):
    """A domain with no rows returns empty lists, not an error."""
    token = await _register_and_get_token(http_client, "dash_domain_none@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    await _seed_dashboard_fixture(async_db, await _user_id(http_client, hdr))

    resp = await http_client.get(
        "/v1/dashboard/domain-summary", params={"domain": "nonexistent"}, headers=hdr
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"domain": "nonexistent", "kpis": [], "primary_anchors": []}
