"""C1 sweep - read-only audit of engine_state health across EVERY state row (INT-15).

Answers what gates deploying C1 (strict decision paths): **would strict loading refuse any
athlete's head, and how much history depends on the legacy columns?**

    python -m app.scripts.audit_engine_state --deployed-sha <sha>          # aggregate report
    python -m app.scripts.audit_engine_state --deployed-sha <sha> --json   # one JSON line
    python -m app.scripts.audit_engine_state --deployed-sha <sha> --verbose  # + row ids

What it reports
---------------
* Every row is classified by the **committed strict codec** (`decode_engine_state`), never by
  hand-written SQL: two implementations of "valid" diverge, and this must agree with the loader
  it clears the way for.
* Heads (the row each decision surface reads) are chosen by the repository's own ordering,
  ``(timestamp, id)`` newest-first, and reported apart from historical rows.
* Missing, incomplete, malformed, unsupported-version and non-finite payloads are counted
  separately, and "athlete with no state row" is counted apart from "a row without canonical
  state" - the first is a new athlete, the second is the problem.
* The deployed SHA (passed in) and the codec version are recorded in the output.
* Aggregates only by default. Raw ``engine_state`` is athlete data and is NEVER printed;
  ``--verbose`` adds row ids, payload hashes and codec codes, never content.

It never proposes backfilling canonical state from the legacy scalars: two different capacity
vectors can project onto identical scalars, so a reconstruction would introduce assumptions,
not restore history.

Where it runs
-------------
Production Postgres has no published port and sits on an isolated Docker network, so this
runs on the host, inside the running API container (it imports only modules that exist at the
deployed SHA, so the new file can be copied in without a deploy):

    cd /opt/stack/infra
    sudo docker compose cp audit_engine_state.py perf-lab-api:/tmp/audit_engine_state.py
    sudo docker compose exec -T -w /app -e PYTHONPATH=/app perf-lab-api \\
        python /tmp/audit_engine_state.py --deployed-sha "$(git -C /opt/stack/perf-lab-api rev-parse HEAD)"

STRICTLY READ-ONLY. Issues SELECTs only, writes nothing, repairs nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import Counter
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
    MAX_READ_VERSION,
    MalformedCurrentEngineState,
    MissingEngineState,
    UnsupportedFutureEngineStateVersion,
    decode_engine_state,
    inspect_declared_version,
    payload_hash,
)
from app.models.athlete_state import AthleteState
from app.models.user import User
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


# --- The C1 sweep: every row, heads by the repository's own ordering ----------------------
#
# Report groups (C1 acceptance). Each classification lands in exactly one group, so the
# counts partition the rows. "missing" is a row WITHOUT canonical state — distinct from an
# athlete with no state row at all, which is counted separately and is not damage.
GROUP_OF: dict[str, str] = {
    "valid_current": "canonical",
    "null_engine_state_legacy_row": "missing",
    "empty_vectors": "incomplete",
    "partial_vectors": "incomplete",
    "malformed_current": "malformed",
    "missing_or_invalid_version": "malformed",
    "unsupported_future_version": "unsupported_version",
    "nonfinite_value": "nonfinite",
}
GROUPS = ("canonical", "missing", "incomplete", "malformed", "unsupported_version", "nonfinite")


def head_key(row: Any) -> tuple[Any, int]:
    """The repository's head ordering, exactly: ``(timestamp, id)``, newest wins.

    ``AthleteContextRepository.get_latest_state`` orders by ``timestamp DESC, id DESC`` — rows
    can share a timestamp (a workout and its e1RM observations), and the id breaks the tie. A
    ``max(timestamp)`` join would report every tied row as a head.
    """
    return (row.timestamp, row.id)


def select_heads(rows: list[Any]) -> dict[int, Any]:
    """Each athlete's head row: the one every decision surface reads."""
    heads: dict[int, Any] = {}
    for r in rows:
        best = heads.get(r.user_id)
        if best is None or head_key(r) > head_key(best):
            heads[r.user_id] = r
    return heads


@dataclass(frozen=True)
class SweepReport:
    deployed_sha: str
    codec_version: str
    users_total: int
    users_without_state: int
    rows_total: int
    heads: dict[str, int]
    historical: dict[str, int]
    head_classes: dict[str, int]
    historical_classes: dict[str, int]
    noncanonical_heads_active_90d: int

    @property
    def noncanonical_heads(self) -> int:
        return sum(n for g, n in self.heads.items() if g != "canonical")

    @property
    def noncanonical_historical(self) -> int:
        return sum(n for g, n in self.historical.items() if g != "canonical")

    def as_dict(self) -> dict[str, Any]:
        return {
            "deployed_sha": self.deployed_sha,
            "codec_version": self.codec_version,
            "users_total": self.users_total,
            "users_without_state": self.users_without_state,
            "rows_total": self.rows_total,
            "heads": self.heads,
            "historical": self.historical,
            "head_classes": self.head_classes,
            "historical_classes": self.historical_classes,
            "noncanonical_heads_active_90d": self.noncanonical_heads_active_90d,
        }


def summarize(
    rows: list[Any],
    *,
    users_total: int,
    active_user_ids: set[int],
    deployed_sha: str,
) -> tuple[SweepReport, list[RowVerdict]]:
    """Classify every row; aggregate heads and history apart. Pure: no database access.

    Returns the aggregate report and the per-row verdicts (ids, hashes and codes only — the
    verdicts never carry a payload). Callers print verdicts only when asked to.
    """
    heads = select_heads(rows)
    head_ids = {r.id for r in heads.values()}
    head_v = [classify(r) for r in heads.values()]
    hist_v = [classify(r) for r in rows if r.id not in head_ids]

    def groups(vs: list[RowVerdict]) -> dict[str, int]:
        c = Counter(GROUP_OF[v.classification] for v in vs)
        return {g: c.get(g, 0) for g in GROUPS}

    report = SweepReport(
        deployed_sha=deployed_sha,
        codec_version=f"v{MAX_READ_VERSION}",
        users_total=users_total,
        users_without_state=users_total - len(heads),
        rows_total=len(rows),
        heads=groups(head_v),
        historical=groups(hist_v),
        head_classes=dict(sorted(Counter(v.classification for v in head_v).items())),
        historical_classes=dict(sorted(Counter(v.classification for v in hist_v).items())),
        noncanonical_heads_active_90d=sum(
            1 for v in head_v
            if v.classification != "valid_current" and v.user_id in active_user_ids
        ),
    )
    return report, head_v + hist_v


def verdict_lines(report: SweepReport) -> tuple[int, list[str]]:
    """Exit code and the operator's reading of the report.

    0 = C1 may deploy (no head would be refused). 1 = it may not, as things stand.
    Nothing here proposes reconstructing canonical state from the legacy scalars: two
    different vectors can share all eight scalars, so a reconstruction creates assumptions,
    it does not restore history.
    """
    lines: list[str] = []
    code = 0
    if report.noncanonical_heads:
        code = 1
        lines += [
            f"BLOCKS C1 DEPLOY: {report.noncanonical_heads} athlete head(s) hold no canonical "
            f"state ({report.noncanonical_heads_active_90d} active in the last 90 days).",
            "  Under C1 each is refused (409) at benchmark, workout, projection and assessment.",
            "  Investigate recovery from trustworthy canonical history or captured events. Any",
            "  reconstruction that introduces assumptions needs its own explicit recovery policy.",
            "  Do NOT backfill canonical state from the legacy scalars.",
        ]
        if report.heads["unsupported_version"]:
            lines.append(
                "  A future-version head is a deployment-ordering fault: deploy readers first; "
                "never repair it."
            )
    else:
        lines.append("CLEAR FOR C1: every athlete head decodes as canonical state.")
    if report.noncanonical_historical:
        lines += [
            f"HISTORY: {report.noncanonical_historical} non-head row(s) hold no canonical state.",
            "  They affect display only. Retain their degraded-display inputs (the legacy",
            "  columns) until an explicit retention or archival decision exists.",
        ]
    if not report.noncanonical_heads and not report.noncanonical_historical:
        lines += [
            "STORAGE: zero rows depend on the legacy columns. No backfill is needed; a later",
            "  drop migration must recheck this itself and refuse if it no longer holds.",
        ]
    return code, lines


async def _active_user_ids(db: AsyncSession, user_ids: set[int]) -> set[int]:
    """Athletes with a workout in the last 90 days. Counts only; no athlete content is read."""
    if not user_ids:
        return set()
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=90)  # logged_at: naive UTC
    rows = await db.execute(
        select(WorkoutLog.user_id)
        .where(WorkoutLog.user_id.in_(user_ids), WorkoutLog.logged_at >= since)
        .distinct()
    )
    return {uid for (uid,) in rows.all()}


async def audit_with_db(
    db: AsyncSession, *, deployed_sha: str, verbose: bool = False, as_json: bool = False
) -> int:
    """Read every state row once, classify, and print the aggregate. Returns the exit code."""
    rows = list((await db.execute(select(AthleteState))).scalars().all())
    users_total = int((await db.execute(select(func.count()).select_from(User))).scalar_one())
    heads = select_heads(rows)
    damaged_head_users = {
        uid for uid, r in heads.items() if classify(r).classification != "valid_current"
    }
    active = await _active_user_ids(db, damaged_head_users)
    report, verdicts = summarize(
        rows, users_total=users_total, active_user_ids=active, deployed_sha=deployed_sha
    )
    code, lines = verdict_lines(report)

    if as_json:
        print(json.dumps({**report.as_dict(), "exit_code": code}, sort_keys=True))
        return code

    print("\n" + "=" * 78)
    print("C1 engine_state sweep (READ-ONLY). Aggregates only; no payload is printed.")
    print("=" * 78)
    print(f"deployed sha        : {report.deployed_sha}")
    print(f"codec version       : {report.codec_version}")
    print(f"users               : {report.users_total}"
          f"  (without any state row: {report.users_without_state})")
    print(f"state rows          : {report.rows_total}"
          f"  (heads: {sum(report.heads.values())}, historical: {sum(report.historical.values())})")
    print()
    print(f"  {'group':<22}{'heads':>8}{'historical':>12}")
    for g in GROUPS:
        print(f"  {g:<22}{report.heads[g]:>8}{report.historical[g]:>12}")
    print()
    print(f"  head classes        : {report.head_classes}")
    print(f"  historical classes  : {report.historical_classes}")
    if verbose:
        print("\n  per-row (ids, hash and codec code only):")
        for v in sorted(verdicts, key=lambda x: (x.user_id, x.state_id)):
            if v.classification != "valid_current":
                print(f"    athlete={v.user_id} state_id={v.state_id} {v.classification} "
                      f"v={v.declared_version} hash={v.hash_} detail={v.detail}")
    print("\n" + "-" * 78)
    for line in lines:
        print(line)
    print()
    return code


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
        "    cd /opt/stack/infra",
        "    sudo docker compose cp audit_engine_state.py perf-lab-api:/tmp/audit_engine_state.py",
        "    sudo docker compose exec -T -w /app -e PYTHONPATH=/app perf-lab-api \\",
        "        python /tmp/audit_engine_state.py --deployed-sha <sha>",
        "",
        "  For a LOCAL run instead (this repo's docker-compose.yml):",
        "    docker compose up -d postgres",
        "    DATABASE_URL='postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab'"
        " python -m app.scripts.audit_engine_state --deployed-sha local",
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
    ap = argparse.ArgumentParser(description="Read-only engine_state sweep (C1).")
    ap.add_argument("--deployed-sha", required=True,
                    help="the SHA the swept database is serving; recorded in the report")
    ap.add_argument("--json", action="store_true", help="one JSON line instead of the table")
    ap.add_argument("--verbose", action="store_true", help="+ row ids, hashes, codec codes")
    args = ap.parse_args()
    try:
        async with AsyncSessionLocal() as db:
            return await audit_with_db(
                db, deployed_sha=args.deployed_sha, verbose=args.verbose, as_json=args.json
            )
    except (OSError, InterfaceError, OperationalError) as exc:
        print(_explain_connect_failure(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
