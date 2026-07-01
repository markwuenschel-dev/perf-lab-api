#!/usr/bin/env python
"""Export offline validation datasets for all 10 research questions.

Usage:
    python scripts/export_validation_datasets.py --output data/exports/

Requires a running database accessible at DATABASE_URL.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab",
)

BUILDERS: dict[str, str] = {
    "Q1_session_decrement": "app.analysis.feature_builders.session_decrement",
    "Q2_fatigue_recovery": "app.analysis.feature_builders.fatigue_recovery",
    "Q3_tissue_risk": "app.analysis.feature_builders.tissue_risk_features",
    "Q4_sleep_stress_residual": "app.analysis.feature_builders.sleep_stress_residual",
    "Q5_benchmark_validity": "app.analysis.feature_builders.benchmark_validity_features",
    "Q6_deload_risk": "app.analysis.feature_builders.deload_risk_features",
    "Q7_experiment": "app.analysis.feature_builders.experiment_features",
    "Q8_scoring_weights": "app.analysis.feature_builders.scoring_weight_features",
    "Q9_interference": "app.analysis.feature_builders.interference_features",
    "Q10_confidence_calibration": "app.analysis.feature_builders.confidence_calibration_features",
}


async def export_all(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(DATABASE_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    for name, module_path in BUILDERS.items():
        print(f"  Building {name}...", end=" ", flush=True)
        try:
            async with factory() as session:
                mod = importlib.import_module(module_path)
                rows = await mod.build_dataset(session)
            out_path = output_dir / f"{name}.jsonl"
            with out_path.open("w") as f:
                for row in rows:
                    f.write(json.dumps(row, default=str) + "\n")
            print(f"{len(rows)} rows → {out_path}")
        except Exception as exc:
            print(f"FAILED {name}: {exc}")
            continue

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export validation datasets for all 10 research questions."
    )
    parser.add_argument(
        "--output",
        default="data/exports",
        help="Output directory for .jsonl files (default: data/exports)",
    )
    args = parser.parse_args()
    asyncio.run(export_all(Path(args.output)))
