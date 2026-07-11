"""Prod-safe catalog seed: benchmark definitions + exercises only.

The assessment surface (ADR-0047) reads `benchmark_definitions`, but nothing seeded that
catalog on boot — migrations create tables, no migration inserts these rows, and the seed
scripts ran nowhere. This composes the two idempotent catalog seeders so every environment
self-seeds. Wired into the image CMD after `alembic upgrade head`.

Idempotent (each underlying `seed()` skips existing rows by code) and fault-tolerant: a
per-step failure is logged and swallowed so a seed hiccup never blocks uvicorn boot.

Deliberately does NOT call `seed_all` — that injects Fitbit/gym/OpenPL *demo athletes*,
which must never land in prod. Exercises run before benchmarks so the benchmark seeder's
`Exercise.e1rm_benchmark_code` backfill can link to existing rows.
"""
from __future__ import annotations

import asyncio
import logging

from app.scripts import seed_benchmarks, seed_exercises

logger = logging.getLogger(__name__)

# (label, module) — `.seed` is resolved at call time so it stays overridable in tests.
# Exercises before benchmarks so the benchmark seeder's e1rm-code backfill can link.
_STEPS = [
    ("exercises", seed_exercises),
    ("benchmarks", seed_benchmarks),
]


async def seed_catalog() -> None:
    """Run the catalog seeders idempotently; never raise (fault-tolerant for boot)."""
    for label, module in _STEPS:
        try:
            await module.seed()
            logger.info("catalog seed step %r ok", label)
        except Exception:
            logger.warning("catalog seed step %r failed (continuing)", label, exc_info=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed_catalog())
