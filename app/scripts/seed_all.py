"""Master seeding pipeline — runs all seeders in dependency order.

Run order:
  1. seed_benchmarks          (definitions must exist before observations)
  2. seed_demo_athletes       (google-fit athletes — the base population)
  3. seed_fitbit_wellness     (enriches google-fit athletes with Fitbit sleep data)
  4. seed_fitbit_workout_logs (adds training history for google-fit athletes)
  5. seed_gym_members_wellness (creates gym-member athlete pool)
  6. seed_openpl_strength     (creates powerlifting athlete pool with real lifts)

Each step is idempotent. Failures are caught per-step and the pipeline continues.

Run (local docker):
    $env:DATABASE_URL = "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab"
    $env:DEBUG = "false"
    python -m app.scripts.seed_all

Options:
    --users N          Number of google-fit demo athletes (default 25)
    --skip-openpl      Skip the OpenPowerlifting step (requires prior download)
"""

from __future__ import annotations

import argparse
import asyncio
import traceback
from collections.abc import Callable, Coroutine
from typing import Any


async def _run_step(
    label: str, coro_fn: Callable[..., Coroutine[Any, Any, None]], **kwargs: Any
) -> bool:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    try:
        await coro_fn(**kwargs)
        return True
    except SystemExit as exc:
        print(f"  [SKIP] {exc}")
        return False
    except Exception:
        traceback.print_exc()
        return False


async def main_async(n_users: int = 25, skip_openpl: bool = False) -> None:
    from app.scripts import (  # noqa: PLC0415
        seed_benchmarks,
        seed_demo_athletes,
        seed_fitbit_wellness,
        seed_fitbit_workout_logs,
        seed_gym_members_wellness,
        seed_openpl_strength,
    )

    results: list[tuple[str, bool]] = []

    steps: list[tuple[str, Callable, dict]] = [
        ("Step 1: Benchmark definitions", seed_benchmarks.seed, {}),
        ("Step 2: Google-Fit demo athletes", seed_demo_athletes.seed, {"n_users": n_users, "runs_per_athlete": 3}),
        ("Step 3: Fitbit sleep wellness", seed_fitbit_wellness.seed, {}),
        ("Step 4: Fitbit workout logs", seed_fitbit_workout_logs.seed, {}),
        ("Step 5: Gym-members athletes", seed_gym_members_wellness.seed, {}),
    ]

    if not skip_openpl:
        steps.append(
            ("Step 6: OpenPowerlifting strength athletes", seed_openpl_strength.seed, {})
        )

    for label, fn, kwargs in steps:
        ok = await _run_step(label, fn, **kwargs)
        results.append((label, ok))

    print(f"\n{'=' * 60}")
    print("  Seeding summary")
    print(f"{'=' * 60}")
    for label, ok in results:
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}] {label}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run all Perf Lab demo seeders in order")
    ap.add_argument("--users", type=int, default=25, help="Google-Fit demo athletes")
    ap.add_argument(
        "--skip-openpl",
        action="store_true",
        default=False,
        help="Skip OpenPowerlifting step (omit if data/kaggle/powerlifting/ not present)",
    )
    args = ap.parse_args()
    asyncio.run(main_async(n_users=args.users, skip_openpl=args.skip_openpl))


if __name__ == "__main__":
    main()
