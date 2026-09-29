"""The driving objective — what actually drives prescription (B2, fork F2).

``GET /v1/objectives/driving`` reports the objective chosen by
``objective_service.resolve_driving_objective``, the same selector whose signals the
prescriber receives. These tests pin the two sources (macrocycle anchor vs. priority
scan), check the reported objective agrees with the signals prescription actually gets,
and guard that ``display_rank`` (display only — not a weight, ADR-0061) never reaches the
prescription path.

DB-backed tests require a live DB (async_db / http_client).
"""
import inspect
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.objective import Objective
from app.models.user import User
from app.schemas.macrocycle import MacrocycleCreate
from app.schemas.objective import ObjectiveCreate
from app.services import macrocycle_service, objective_service
from app.services.prescription_service import _gather_prescription_context

REPO_ROOT = Path(__file__).resolve().parents[1]


async def _mk_user(db: AsyncSession, email: str) -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _mk_objective(
    db: AsyncSession, user_id: int, *, label: str, priority: int, domain: str | None,
    target_in_days: int | None = None,
) -> Objective:
    return await objective_service.create_objective(
        db,
        user_id,
        ObjectiveCreate(
            label=label,
            priority=priority,
            domain=domain,
            target_date=(date.today() + timedelta(days=target_in_days))
            if target_in_days is not None
            else None,
        ),
    )


@pytest.mark.asyncio
async def test_no_objectives_means_nothing_drives(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "drive_none@test.com")
    driving = await objective_service.resolve_driving_objective(async_db, user.id)
    assert driving.objective is None
    assert driving.source is None
    assert driving.signals == {"taper": False, "domain": None}


@pytest.mark.asyncio
async def test_priority_source_picks_top_priority_not_display_rank(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "drive_prio@test.com")
    top = await _mk_objective(async_db, user.id, label="Top", priority=1, domain="strength")
    other = await _mk_objective(async_db, user.id, label="Other", priority=2, domain="running")

    # Rank the lower-priority objective first for display: must not change what drives.
    await objective_service.set_display_order(async_db, user.id, [other.id, top.id])

    driving = await objective_service.resolve_driving_objective(async_db, user.id)
    assert driving.source == "priority"
    assert driving.objective is not None and driving.objective.id == top.id
    assert driving.signals["domain"] == "strength"


@pytest.mark.asyncio
async def test_anchor_source_overrides_priority(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "drive_anchor@test.com")
    await _mk_objective(async_db, user.id, label="Top", priority=1, domain="strength")
    anchor = await _mk_objective(
        async_db, user.id, label="Race", priority=3, domain="running", target_in_days=10
    )
    later = await _mk_objective(async_db, user.id, label="Later", priority=2, domain="hypertrophy")
    # Earliest-start active macrocycle wins (list_macrocycles order): anchor starts first.
    await macrocycle_service.create_macrocycle(
        async_db, user.id,
        MacrocycleCreate(objective_id=later.id, start_date=date.today() + timedelta(days=3)),
    )
    await macrocycle_service.create_macrocycle(
        async_db, user.id, MacrocycleCreate(objective_id=anchor.id, start_date=date.today())
    )

    driving = await objective_service.resolve_driving_objective(async_db, user.id)
    assert driving.source == "macrocycle_anchor"
    assert driving.objective is not None and driving.objective.id == anchor.id
    assert driving.signals == {"taper": True, "domain": "running"}


@pytest.mark.parametrize("with_macrocycle", [False, True])
@pytest.mark.asyncio
async def test_driving_agrees_with_signals_prescription_receives(
    async_db: AsyncSession, with_macrocycle: bool
) -> None:
    """The seam: the objective reported as driving is the one whose domain lands in the
    prescriber's block_context (prescription_service._gather_prescription_context)."""
    user = await _mk_user(async_db, f"drive_seam_{with_macrocycle}@test.com")
    await _mk_objective(async_db, user.id, label="Top", priority=1, domain="strength")
    anchor = await _mk_objective(async_db, user.id, label="Anchor", priority=4, domain="running")
    if with_macrocycle:
        await macrocycle_service.create_macrocycle(
            async_db, user.id, MacrocycleCreate(objective_id=anchor.id, start_date=date.today())
        )

    driving = await objective_service.resolve_driving_objective(async_db, user.id)
    ctx = await _gather_prescription_context(async_db, user.id, goal=None, planned_session=None)

    assert driving.objective is not None
    assert ctx.block_context["objective_domain"] == driving.objective.domain
    assert ctx.block_context["objective_taper"] == driving.signals["taper"]
    assert driving.signals == await objective_service.active_objective_signals(async_db, user.id)
    assert driving.objective.domain == ("running" if with_macrocycle else "strength")


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


@pytest.mark.asyncio
async def test_driving_route_shape(http_client) -> None:
    token = await _register_and_get_token(http_client, "drive_route@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    empty = await http_client.get("/v1/objectives/driving", headers=hdr)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"objective_id": None, "source": None}

    top = (
        await http_client.post("/v1/objectives", json={"label": "Top", "priority": 1}, headers=hdr)
    ).json()
    anchor = (
        await http_client.post("/v1/objectives", json={"label": "Meet", "priority": 3}, headers=hdr)
    ).json()
    assert (await http_client.get("/v1/objectives/driving", headers=hdr)).json() == {
        "objective_id": top["id"],
        "source": "priority",
    }

    made = await http_client.post(
        "/v1/macrocycles", json={"objective_id": anchor["id"]}, headers=hdr
    )
    assert made.status_code == 200, made.text
    assert (await http_client.get("/v1/objectives/driving", headers=hdr)).json() == {
        "objective_id": anchor["id"],
        "source": "macrocycle_anchor",
    }


# ---------------------------------------------------------------------------
# Guard — display_rank is display only, never a weight (ADR-0061)
# ---------------------------------------------------------------------------

def test_display_rank_never_reaches_the_prescription_path() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "app" / "logic").rglob("*.py"))
        if "display_rank" in path.read_text(encoding="utf-8")
    ]
    prescription_service = REPO_ROOT / "app" / "services" / "prescription_service.py"
    if "display_rank" in prescription_service.read_text(encoding="utf-8"):
        offenders.append(str(prescription_service.relative_to(REPO_ROOT)))
    for selector in (
        objective_service.resolve_driving_objective,
        objective_service.active_objective_signals,
        objective_service.signals_from_scan,
        objective_service.signals_from_anchor,
        objective_service.top_priority_objective,
    ):
        if "display_rank" in inspect.getsource(selector):
            offenders.append(f"objective_service.{selector.__name__}")
    assert offenders == [], f"display_rank is display only (ADR-0061); found in {offenders}"
