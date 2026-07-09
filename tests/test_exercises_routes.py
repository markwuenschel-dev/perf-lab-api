"""Route tests for the read-only exercise catalog (ADR-0045 log UI)."""
import pytest

from app.models.exercise import Exercise

pytestmark = pytest.mark.asyncio


async def _seed(db) -> None:
    db.add_all([
        Exercise(name="Back Squat", modality="Strength", movement_pattern="squat",
                 load_type="barbell", is_benchmark=True, e1rm_benchmark_code="pl_e1rm_squat"),
        Exercise(name="Easy Run", modality="Running", movement_pattern="run", load_type="distance"),
        Exercise(name="Push-up", modality="Calisthenics", movement_pattern="push_horizontal",
                 load_type="bodyweight"),
    ])
    await db.commit()


async def test_list_exercises_returns_catalog(async_db, http_client):
    await _seed(async_db)
    resp = await http_client.get("/v1/exercises")
    assert resp.status_code == 200, resp.text
    names = [e["name"] for e in resp.json()]
    assert names == ["Back Squat", "Easy Run", "Push-up"]  # ordered by name
    squat = next(e for e in resp.json() if e["name"] == "Back Squat")
    assert squat["load_type"] == "barbell"
    assert squat["e1rm_benchmark_code"] == "pl_e1rm_squat"


async def test_list_exercises_filters(async_db, http_client):
    await _seed(async_db)
    assert [e["name"] for e in (await http_client.get("/v1/exercises?q=squat")).json()] == ["Back Squat"]
    assert [e["name"] for e in (await http_client.get("/v1/exercises?load_type=distance")).json()] == ["Easy Run"]
    assert [e["name"] for e in (await http_client.get("/v1/exercises?modality=Calisthenics")).json()] == ["Push-up"]
