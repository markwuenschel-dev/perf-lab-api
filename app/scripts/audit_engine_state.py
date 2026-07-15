"""GATE 1 - read-only prevalence audit of engine_state health (INT-15 W1-A).

Answers the only question that gates strict deployment: **how many athletes have state
the strict codec refuses, and did any of them train recently?**

    python -m app.scripts.audit_engine_state              # summary
    python -m app.scripts.audit_engine_state --verbose    # + per-row identifiers

Production Postgres is not published externally: no host port mapping, and it sits on an
isolated Docker network reachable only from containers attached to it. There is therefore
no external URL and no tunnel, and this CANNOT be run against production from a laptop by
design. Run it on the host, in a container on that network:

    cd ~/infra
    git -C ../perf-lab-api fetch origin && git -C ../perf-lab-api checkout <branch>
    docker compose run --rm --build perf-lab-api python -m app.scripts.audit_engine_state

compose injects DATABASE_URL and attaches the container to `data`. The script prints this
if it cannot connect. Ignore `.env`'s DATABASE_URL - it is stale and points at a provider
this project does not use.

STRICTLY READ-ONLY. Opens no transaction, writes nothing, repairs nothing.

Classification uses the **committed strict codec** (`decode_engine_state`) rather than
hand-written SQL. Two implementations of "valid" diverge, and the SQL copy is the one that
would be wrong - this audit must agree with the loader it is clearing the way for, by
construction rather than by review.

Raw ``engine_state`` is athlete data and is NEVER printed. Rows are identified by id and
``payload_hash()``, which is also what the repair tool compares-and-swaps on.

Only the LATEST state row per athlete drives the verdict - that is the row every decision
surface actually reads. Older damaged rows are reported separately: they matter to history
display, not to whether strict loading blocks a live athlete.
"""

from __future__ import annotations

import argparse
import asyncio
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.engine.engine_state_codec import (
    MalformedCurrentEngineState,
    MissingEngineState,
    UnsupportedFutureEngineStateVersion,
    decode_engine_state,
    inspect_declared_version,
    payload_hash,
)
from app.models.athlete_state import AthleteState
from app.models.benchmark_observation import BenchmarkObservation
from app.models.workout_log import WorkoutLog

# Classes that BLOCK 2B if any active athlete is in them: the row is damaged, and strict
# loading will refuse it at every decision surface until it is repaired.
BLOCKING = {"empty_vectors", "partial_vectors", "malformed_current", "nonfinite_value"}

# Not corruption. A legacy-migration population needing attested backfill before strict.
MIGRATION = {"null_engine_state_legacy_row"}

# Not damaged data - a reader too old for it. Never repaired; stop the writer rollout.
DEPLOYMENT = {"unsupported_future_version"}

_LEGACY_SCALARS = (
    "c_met_aerobic",
    "c_nm_force",
    "c_struct",
    "b_met_anaerobic",
    "f_met_systemic",
    "f_nm_peripheral",
    "f_nm_central",
    "f_struct_damage",
)


@dataclass(frozen=True)
class RowVerdict:
    state_id: int
    user_id: int
    classification: str
    declared_version: int | None
    hash_: str
    detail: str


def _nonfinite_scalars(row: AthleteState) -> list[str]:
    bad = []
    for name in _LEGACY_SCALARS:
        v = getattr(row, name, None)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isfinite(v):
            bad.append(name)
    return bad


def _nonfinite_in_payload(payload: Any) -> bool:
    """Is there a non-finite anywhere in the payload?

    The codec DOES reject these - pydantic's ge/le constraints fail against NaN - so this is
    not a net for something the codec misses. It exists to name the cause: the codec can only
    report a generic `vector_validation_failed`, and "there is a NaN in x" is what a repair
    operator can act on. It also still catches a field that has no range constraint, where
    NaN would otherwise validate and then poison every comparison downstream (`nan > x` is
    False).
    """
    if isinstance(payload, dict):
        return any(_nonfinite_in_payload(v) for v in payload.values())
    if isinstance(payload, list):
        return any(_nonfinite_in_payload(v) for v in payload)
    if isinstance(payload, float):
        return not math.isfinite(payload)
    return False


def classify(row: AthleteState) -> RowVerdict:
    payload = row.engine_state
    declared = inspect_declared_version(payload)
    h = payload_hash(payload)

    def verdict(cls: str, detail: str = "") -> RowVerdict:
        return RowVerdict(row.id, row.user_id, cls, declared, h, detail)

    bad_scalars = _nonfinite_scalars(row)

    try:
        decode_engine_state(payload)
    except MissingEngineState:
        if bad_scalars:
            # A legacy row whose legacy scalars are themselves unusable: it cannot be
            # backfilled from the mirror either. Worse than a plain migration row.
            return verdict("nonfinite_value", f"legacy row, unusable scalars: {','.join(bad_scalars)}")
        return verdict("null_engine_state_legacy_row", "no payload; bootstraps from scalars today")
    except UnsupportedFutureEngineStateVersion as exc:
        # Checked BEFORE any structural inspection below, deliberately: a future payload
        # must never be sniffed. We cannot know what its fields mean, so "is there a NaN in
        # it" is not a question we are entitled to ask.
        return verdict("unsupported_future_version", f"declares v{exc.declared_version}")
    except MalformedCurrentEngineState as exc:
        code = exc.error_code
        # A non-finite inside a vector surfaces from the codec as a generic
        # `vector_validation_failed` (pydantic's ge/le constraints reject NaN before any
        # of our own checks see it). Report the specific cause: "malformed" tells a repair
        # operator nothing, "there is a NaN in x" tells them exactly what to fix.
        if _nonfinite_in_payload(payload):
            return verdict("nonfinite_value", f"non-finite inside vectors ({code})")
        cls = {
            "vector_empty": "empty_vectors",
            "missing_vectors": "partial_vectors",
        }.get(code, "malformed_current")
        if declared is None and code not in ("vector_empty", "missing_vectors"):
            cls = "missing_or_invalid_version"
        return verdict(cls, code)

    if bad_scalars:
        return verdict("nonfinite_value", f"legacy scalars: {','.join(bad_scalars)}")
    if _nonfinite_in_payload(payload):
        return verdict("nonfinite_value", "non-finite inside engine_state vectors")
    return verdict("valid_current", "")


async def _activity(db: AsyncSession, user_ids: set[int]) -> dict[int, dict[str, Any]]:
    """Aggregate recent-activity indicators only. No athlete content is read."""
    if not user_ids:
        return {}
    now = datetime.now(UTC)
    d30, d90 = now - timedelta(days=30), now - timedelta(days=90)
    out: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"workouts_30d": 0, "workouts_90d": 0, "benchmarks_90d": 0, "last_activity": None}
    )

    rows = (
        await db.execute(
            select(WorkoutLog.user_id, func.count(), func.max(WorkoutLog.logged_at))
            .where(WorkoutLog.user_id.in_(user_ids), WorkoutLog.logged_at >= d90)
            .group_by(WorkoutLog.user_id)
        )
    ).all()
    for uid, count, last in rows:
        out[uid]["workouts_90d"] = count
        out[uid]["last_activity"] = last

    rows = (
        await db.execute(
            select(WorkoutLog.user_id, func.count())
            .where(WorkoutLog.user_id.in_(user_ids), WorkoutLog.logged_at >= d30)
            .group_by(WorkoutLog.user_id)
        )
    ).all()
    for uid, count in rows:
        out[uid]["workouts_30d"] = count

    rows = (
        await db.execute(
            select(BenchmarkObservation.user_id, func.count())
            .where(BenchmarkObservation.user_id.in_(user_ids))
            .group_by(BenchmarkObservation.user_id)
        )
    ).all()
    for uid, count in rows:
        out[uid]["benchmarks_90d"] = count

    return out


async def audit_with_db(db: AsyncSession, verbose: bool) -> int:
    """Returns a shell exit code: 0 = clear to proceed, 1 = 2B is blocked."""
    latest_ts = (
        select(AthleteState.user_id, func.max(AthleteState.timestamp).label("ts"))
        .group_by(AthleteState.user_id)
        .subquery()
    )
    latest_rows = (
        await db.execute(
            select(AthleteState).join(
                latest_ts,
                (AthleteState.user_id == latest_ts.c.user_id)
                & (AthleteState.timestamp == latest_ts.c.ts),
            )
        )
    ).scalars().all()
    all_rows = (await db.execute(select(AthleteState))).scalars().all()

    latest = [classify(r) for r in latest_rows]
    historical = [classify(r) for r in all_rows if r.id not in {v.state_id for v in latest}]

    counts = Counter(v.classification for v in latest)
    affected = [v for v in latest if v.classification != "valid_current"]
    activity = await _activity(db, {v.user_id for v in affected})

    print("\n" + "=" * 78)
    print("GATE 1 - engine_state prevalence (READ-ONLY). Latest row per athlete.")
    print("=" * 78)
    print(f"athletes with state : {len(latest)}")
    print(f"total state rows    : {len(all_rows)}")
    print()
    for cls in sorted(counts):
        print(f"  {cls:<32} {counts[cls]:>6}")

    blocked: list[RowVerdict] = []
    print("\n" + "-" * 78)
    print("Affected athletes - recent activity (last_activity = most recent workout)")
    print("-" * 78)
    if not affected:
        print("  none")
    for v in sorted(affected, key=lambda x: x.classification):
        act = activity.get(v.user_id, {})
        w30 = act.get("workouts_30d", 0)
        w90 = act.get("workouts_90d", 0)
        bm = act.get("benchmarks_90d", 0)
        last = act.get("last_activity")
        active = bool(w90)
        if active and v.classification in BLOCKING:
            blocked.append(v)
        flag = "BLOCKS-2B" if (active and v.classification in BLOCKING) else ""
        print(
            f"  athlete={v.user_id:<6} state_id={v.state_id:<8} {v.classification:<28} "
            f"v={v.declared_version} w30={w30:<4} w90={w90:<4} bm={bm:<4} "
            f"last={last} {flag}"
        )
        if verbose:
            print(f"      hash={v.hash_}  detail={v.detail}")

    hist_counts = Counter(v.classification for v in historical)
    hist_bad = {k: n for k, n in hist_counts.items() if k != "valid_current"}
    print("\n" + "-" * 78)
    print("Historical (non-latest) rows - affect display only, not decision authority")
    print("-" * 78)
    print(f"  {hist_bad or 'all valid'}")

    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    migration = [v for v in latest if v.classification in MIGRATION]
    future = [v for v in latest if v.classification in DEPLOYMENT]

    if blocked:
        print(f"  BLOCKED: {len(blocked)} ACTIVE athlete(s) hold damaged latest state.")
        print("    Required: inspect -> repair via the forensic path -> validate through the")
        print("    strict codec -> compare-and-swap. Only then deploy 2B.")
    if migration:
        print(f"  BACKFILL REQUIRED: {len(migration)} athlete(s) on NULL engine_state.")
        print("    A legacy-migration population, not corruption - they bootstrap from legacy")
        print("    scalars today and will be refused under strict. Backfill through the exact")
        print("    versioned reconstruction, marked compatibility-derived in provenance. Do NOT")
        print("    record it as an originally observed vector; the scalar projection is lossy.")
    if future:
        print(f"  DEPLOYMENT ORDERING FAULT: {len(future)} athlete(s) hold future-version state.")
        print("    Do NOT repair or reconstruct. A newer writer is already live against older")
        print("    readers. Stop the writer rollout; deploy readers first.")
    if not (blocked or migration or future):
        print("  CLEAR: no active athlete is affected. 2B may proceed - GATE 2 (e1RM")
        print("  transaction ownership) is already closed, so this was the last gate.")
    print()

    return 1 if (blocked or migration or future) else 0


def _explain_connect_failure(exc: Exception) -> str:
    """Turn a 60-line asyncpg traceback into the one fact that matters.

    Production Postgres has no published port and sits on an isolated Docker network,
    reachable only by containers attached to it. The consequence for this script: there is
    no external URL and no tunnel. It cannot be run against production from a laptop, by
    design. It runs on the host.
    """
    host = ""
    try:
        host = make_url(settings.DATABASE_URL).host or ""
    except Exception:  # noqa: BLE001 - diagnostics must never mask the original error
        pass

    lines = ["", "Could not connect to the database.", f"  host: {host or '<unparseable>'}"]
    lines += [
        f"  error: {type(exc).__name__}: {exc}",
        "",
        "  Production Postgres has no published port and lives on an isolated Docker",
        "  network. There is no external URL to point at - by design. Run this on the",
        "  host, in a container attached to that network:",
        "",
        "    cd ~/infra",
        "    git -C ../perf-lab-api fetch origin",
        "    git -C ../perf-lab-api checkout <branch>",
        "    docker compose run --rm --build perf-lab-api \\",
        "        python -m app.scripts.audit_engine_state",
        "",
        "  compose injects DATABASE_URL and attaches the container to `data`.",
        "",
        "  For a LOCAL run instead (this repo's docker-compose.yml):",
        "    docker compose up -d postgres",
        "    DATABASE_URL='postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab'"
        " python -m app.scripts.audit_engine_state",
    ]
    if host.startswith("dpg-"):
        lines += [
            "",
            "  NOTE: that hostname belongs to a provider this project does not use.",
            "  Your .env DATABASE_URL is stale. Production config is injected by the",
            "  deployment's compose file, not read from .env.",
        ]
    lines += ["", "This audit is READ-ONLY - it is safe to point at production.", ""]
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only engine_state prevalence audit.")
    ap.add_argument("--verbose", action="store_true", help="per-row hash and codec detail")
    args = ap.parse_args()
    try:
        async with AsyncSessionLocal() as db:
            return await audit_with_db(db, args.verbose)
    except (OSError, InterfaceError, OperationalError) as exc:
        print(_explain_connect_failure(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
