"""seed_catalog is fault-tolerant — a seed hiccup must never block boot.

Idempotency + actual row counts are proven against Postgres manually (run twice → 0 new
rows); this pins the critical boot-safety invariant purely, no DB required.
"""
import pytest

from app.scripts import seed_catalog


@pytest.mark.asyncio
async def test_seed_catalog_swallows_step_failures(monkeypatch):
    calls: list[str] = []

    async def ok() -> None:
        calls.append("ok")

    async def boom() -> None:
        calls.append("boom")
        raise RuntimeError("simulated seed failure")

    # Even if a step raises, seed_catalog must complete without propagating — otherwise a
    # bad seed would crash the container before uvicorn starts.
    monkeypatch.setattr(seed_catalog.seed_exercises, "seed", boom)
    monkeypatch.setattr(seed_catalog.seed_benchmarks, "seed", ok)

    await seed_catalog.seed_catalog()  # must not raise

    # Both steps were attempted despite the first failing.
    assert calls == ["boom", "ok"]


@pytest.mark.asyncio
async def test_seed_catalog_runs_all_steps_on_success(monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(seed_catalog.seed_exercises, "seed", lambda: _record(ran, "ex"))
    monkeypatch.setattr(seed_catalog.seed_benchmarks, "seed", lambda: _record(ran, "bench"))
    await seed_catalog.seed_catalog()
    assert ran == ["ex", "bench"]  # exercises before benchmarks (e1rm-link ordering)


async def _record(bucket: list[str], name: str) -> None:
    bucket.append(name)
