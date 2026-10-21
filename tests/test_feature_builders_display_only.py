"""B2 — no training frame ingests a display-only observation (live DB).

The four generic builders read every benchmark observation. A 1.5 mi time trial's raw value is a
time in seconds with no authority, so a future fit must never see it as an outcome. Each builder
runs against both kinds of row; only the ordinary one may come back.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.feature_builders import (
    benchmark_validity_features,
    confidence_calibration_features,
    interference_features,
    sleep_stress_residual,
)
from app.logic import vo2_estimate as vo2
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.user import User
from app.scripts.seed_benchmarks import BENCHMARKS

pytestmark = pytest.mark.asyncio

BUILDERS = [
    benchmark_validity_features,
    confidence_calibration_features,
    interference_features,
    sleep_stress_residual,
]


async def test_every_generic_builder_leaves_display_only_rows_out(async_db: AsyncSession) -> None:
    by_code = {row["code"]: row for row in BENCHMARKS}
    old = BenchmarkDefinition(**by_code["run_vo2_field_test_300m_1p5mi"])
    new = BenchmarkDefinition(**by_code[vo2.PROTOCOL_CODE])
    user = User(email="ml_frames@test.com", hashed_password="x", is_active=True)
    async_db.add_all([old, new, user])
    await async_db.flush()
    for definition, raw in ((old, 44.0), (new, 720.0), (old, 45.0), (new, 700.0)):
        async_db.add(BenchmarkObservation(
            user_id=user.id, benchmark_definition_id=definition.id, raw_value=raw,
            observed_at=datetime(2026, 9, 1, 8, 0), validity_status="valid", source="manual",
        ))
    await async_db.commit()

    for builder in BUILDERS:
        rows = await builder.build_dataset(async_db)
        name = builder.__name__
        assert rows, f"{name}: the ordinary rows must still come back"
        assert sorted(r["raw_value"] for r in rows) == [44.0, 45.0], name
        for r in rows:
            if "benchmark_code" in r:
                assert r["benchmark_code"] != vo2.PROTOCOL_CODE, name
            if "benchmark_definition_id" in r:
                assert r["benchmark_definition_id"] != new.id, name
