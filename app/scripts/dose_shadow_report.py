"""Report what the dose-model shadow log (phase 8A) has captured: a census, then v1/v0 ratios.

Read-only, and it authorizes nothing. A ratio says how differently v1 scores a session than
v0 does, not which of them is right. Deciding that is phase 8B's fit and 8C's validation, and
this report only tells you whether there is enough real data to start.

The CENSUS answers that question the way a repeated-measures fit needs it answered: not a raw
row count, but usable pairs (``app/logic/dose_fit_policy.py``, the same rules the training
frame applies), distinct athletes, depth per athlete, how many athletes could be held out
independently, and coverage. Seeded accounts (``@perflab.local``) are counted separately and
never pooled with real athletes. Rows of different ``v1_model_version`` are never pooled either.

Rows where v0 counted fabricated sets (``v0_volume_used_fabricated_sets``) are excluded by
default. Their v0 side is not a measurement of the session (docs/calibration-backlog.md, C2).

Run (against a local DB):
    $env:DATABASE_URL = "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab"
    python -m app.scripts.dose_shadow_report
    python -m app.scripts.dose_shadow_report --json --include-fabricated

Always exits 0. With an empty table, "no rows" is the expected answer, not a failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.logic.dose_fit_policy import (
    FIT_POLICY_VERSION,
    MIN_PAIRS_PER_HELD_OUT_ATHLETE,
    LoggedSession,
    SessionPair,
    fit_tier,
    is_seeded_email,
    pair_sessions,
    pairing_rule,
)
from app.models.dose_model_shadow import DoseModelShadowLog

#: The columns the report reads. Kept narrow: the JSON detail columns are not needed here.
REPORT_COLUMNS: tuple[str, ...] = (
    "user_id",
    "workout_log_id",
    "session_at",
    "v1_model_version",
    "v1_density_basis",
    "code_version",
    "modality",
    "planned_domain",
    "planned_category",
    "experience_level",
    "workload_preference",
    "workload_preference_defaulted",
    "prescription_branch",
    "duration_minutes",
    "reported_sets",
    "ratio_v1_v0",
    "v1_density_not_modelled",
    "v0_volume_used_fabricated_sets",
)

HEADER = (
    "Dose shadow log (8A): v1/v0 total-dose ratios. Read-only; authorizes nothing. "
    "A ratio says how v1 differs from v0, not which is right."
)


def _duration_bucket(minutes: float | None) -> str:
    if minutes is None:
        return "unknown"
    if minutes < 30:
        return "<30 min"
    if minutes < 60:
        return "30-59 min"
    if minutes < 90:
        return "60-89 min"
    return ">=90 min"


def _sets_bucket(sets: float | None) -> str:
    # Missing is its own bucket, never zero: an unreported set count is not "no sets".
    if sets is None:
        return "not reported"
    if sets < 10:
        return "<10 sets"
    if sets < 20:
        return "10-19 sets"
    return ">=20 sets"


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks; ``sorted_values`` must be non-empty."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _ratio_stats(ratios: list[float]) -> dict[str, float | int]:
    s = sorted(ratios)
    return {
        "n": len(s),
        "median": _percentile(s, 0.5),
        "p10": _percentile(s, 0.1),
        "p90": _percentile(s, 0.9),
        "min": s[0],
        "max": s[-1],
    }


#: Each grouping: name → how a row maps to its group label.
GROUPINGS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "modality": lambda r: str(r.get("modality") or "unknown"),
    "planned_domain": lambda r: str(r.get("planned_domain") or "unplanned"),
    "planned_category": lambda r: str(r.get("planned_category") or "unplanned"),
    "duration": lambda r: _duration_bucket(r.get("duration_minutes")),
    "reported_sets": lambda r: _sets_bucket(r.get("reported_sets")),
    "v1_density_not_modelled": lambda r: str(bool(r.get("v1_density_not_modelled"))).lower(),
    # A ratio under v1.1 and one under v1.2 are different models' answers: never one bucket.
    "v1_model_version": lambda r: str(r.get("v1_model_version") or "unknown"),
    # Prescribed-proxy density is modelled but excluded from fitting; it must not hide
    # inside the "modelled" bucket above.
    "v1_density_basis": lambda r: str(r.get("v1_density_basis") or "unknown"),
}


def summarize(
    rows: Iterable[Mapping[str, Any]], *, include_fabricated: bool = False
) -> dict[str, Any]:
    """Pure aggregation over shadow rows (mappings with the ``REPORT_COLUMNS`` keys)."""
    all_rows = list(rows)
    if not all_rows:
        return {"status": "no_rows", "total_rows": 0}

    fabricated = [r for r in all_rows if r.get("v0_volume_used_fabricated_sets")]
    kept = all_rows if include_fabricated else [
        r for r in all_rows if not r.get("v0_volume_used_fabricated_sets")
    ]

    session_times: list[datetime] = [r["session_at"] for r in kept if r.get("session_at")]
    overview: dict[str, Any] = {
        "total_rows": len(all_rows),
        "fabricated_set_rows": len(fabricated),
        "fabricated_set_rows_excluded": 0 if include_fabricated else len(fabricated),
        "analysed_rows": len(kept),
        "distinct_users": len({r.get("user_id") for r in kept}),
        "session_at_first": min(session_times).isoformat() if session_times else None,
        "session_at_last": max(session_times).isoformat() if session_times else None,
        # Rows written before the deploy passed APP_BUILD_SHA carry no build identity.
        "rows_without_code_version": sum(1 for r in all_rows if not r.get("code_version")),
        # ratio is None when v0_total is zero: undefined, so it is counted, never averaged.
        "rows_with_undefined_ratio": sum(1 for r in kept if r.get("ratio_v1_v0") is None),
    }
    if not kept:
        return {"status": "no_rows_after_exclusion", **overview}

    defined = [r for r in kept if r.get("ratio_v1_v0") is not None]
    groups: dict[str, dict[str, dict[str, float | int]]] = {}
    for name, key in GROUPINGS.items():
        buckets: dict[str, list[float]] = defaultdict(list)
        for r in defined:
            buckets[key(r)].append(float(r["ratio_v1_v0"]))
        groups[name] = {label: _ratio_stats(vals) for label, vals in sorted(buckets.items())}

    return {
        "status": "ok",
        **overview,
        "overall": _ratio_stats([float(r["ratio_v1_v0"]) for r in defined]) if defined else None,
        "groups": groups,
    }


# --- Census: is there enough real, label-able longitudinal data to fit? -----------------

#: Capture fields whose null rate the census reports. A null here is missing provenance.
CAPTURE_FIELDS: tuple[str, ...] = (
    "workout_log_id",
    "planned_domain",
    "planned_category",
    "reported_sets",
    "code_version",
    # a047 (phase 8.2). Null on every row written before that migration.
    "experience_level",
    "workload_preference",
    "prescription_branch",
)

#: Coverage dimensions: how the eligible rows spread across what a fit must generalise over.
COVERAGE: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "modality": lambda r: str(r.get("modality") or "unknown"),
    "planned_category": lambda r: str(r.get("planned_category") or "unplanned"),
    # Absent values are named for WHY they are absent, never a bare "null" bucket.
    "experience_level": lambda r: str(r.get("experience_level") or "no_profile_or_pre_a047"),
    "workload_preference": lambda r: (
        f"{r['workload_preference']} (default)"
        if r.get("workload_preference") and r.get("workload_preference_defaulted")
        else str(r.get("workload_preference") or "no_block_or_pre_a047")
    ),
    "prescription_branch": lambda r: str(r.get("prescription_branch") or "unplanned_or_pre_a047"),
}

DEPTH_THRESHOLDS: tuple[int, ...] = (2, 3, 5, 10)


def _median(values: list[int]) -> float | None:
    return _percentile(sorted(float(v) for v in values), 0.5) if values else None


def _version_census(
    vrows: list[Mapping[str, Any]], pairs: Mapping[int, SessionPair]
) -> dict[str, Any]:
    tiers: dict[str, int] = defaultdict(int)
    bases: dict[str, int] = defaultdict(int)
    eligible_per_user: dict[int, int] = defaultdict(int)
    labelled_per_user: dict[int, int] = defaultdict(int)
    pair_status: dict[str, int] = defaultdict(int)
    coverage: dict[str, dict[str, dict[str, int]]] = {k: {} for k in COVERAGE}
    for r in vrows:
        tier = fit_tier(
            v0_volume_used_fabricated_sets=bool(r.get("v0_volume_used_fabricated_sets")),
            v1_density_basis=r.get("v1_density_basis"),
        )
        tiers[tier] += 1
        bases[str(r.get("v1_density_basis") or "unknown")] += 1
        if tier != "eligible":
            continue
        uid = int(r["user_id"])
        eligible_per_user[uid] += 1
        wl = r.get("workout_log_id")
        pair = pairs.get(int(wl)) if wl is not None else None
        status = pair.status if pair is not None else "unlinked_workout_log"
        pair_status[status] += 1
        labelled = status == "labelled"
        if labelled:
            labelled_per_user[uid] += 1
        for dim, key in COVERAGE.items():
            cell = coverage[dim].setdefault(key(r), {"eligible_rows": 0, "labelled_pairs": 0})
            cell["eligible_rows"] += 1
            cell["labelled_pairs"] += int(labelled)

    per_athlete_pairs = [labelled_per_user.get(u, 0) for u in eligible_per_user]
    return {
        "rows": len(vrows),
        "athletes": len({r.get("user_id") for r in vrows}),
        "rows_by_fit_tier": dict(sorted(tiers.items())),
        "rows_by_density_basis": dict(sorted(bases.items())),
        "eligible_rows": tiers.get("eligible", 0),
        "athletes_with_eligible_rows": len(eligible_per_user),
        "athletes_with_at_least_n_eligible_sessions": {
            str(n): sum(1 for c in eligible_per_user.values() if c >= n) for n in DEPTH_THRESHOLDS
        },
        "eligible_rows_by_pair_status": dict(sorted(pair_status.items())),
        "labelled_pairs": sum(labelled_per_user.values()),
        "labelled_pairs_per_athlete_median": _median(per_athlete_pairs),
        "labelled_pairs_per_athlete_max": max(per_athlete_pairs, default=0),
        # How many athletes carry enough pairs to be held out on their own.
        "athletes_holdout_capable": sum(
            1 for c in per_athlete_pairs if c >= MIN_PAIRS_PER_HELD_OUT_ATHLETE
        ),
        "coverage": {dim: dict(sorted(cells.items())) for dim, cells in coverage.items()},
    }


def _population_census(
    rows: list[Mapping[str, Any]], pairs: Mapping[int, SessionPair]
) -> dict[str, Any]:
    """Census of one population (real or seeded), split by ``v1_model_version``."""
    by_version: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_version[str(r.get("v1_model_version") or "unknown")].append(r)

    session_times: list[datetime] = [r["session_at"] for r in rows if r.get("session_at")]
    return {
        "rows": len(rows),
        "athletes": len({r.get("user_id") for r in rows}),
        "session_at_first": min(session_times).isoformat() if session_times else None,
        "session_at_last": max(session_times).isoformat() if session_times else None,
        "null_rate": {
            f: (sum(1 for r in rows if r.get(f) in (None, "")) / len(rows)) if rows else None
            for f in CAPTURE_FIELDS
        },
        "by_v1_model_version": {
            v: _version_census(vrows, pairs) for v, vrows in sorted(by_version.items())
        },
    }


def census(
    rows: Iterable[Mapping[str, Any]],
    sessions: Iterable[LoggedSession],
    seeded_user_ids: Iterable[int],
) -> dict[str, Any]:
    """Pure census over shadow rows plus ALL logged sessions (for next session and baseline).

    ``rows`` carry the ``REPORT_COLUMNS`` keys. Real and seeded athletes are reported as two
    populations; only ``real`` could ever justify a fit.
    """
    seeded = set(seeded_user_ids)
    pairs = pair_sessions(sessions)
    all_rows = list(rows)
    return {
        "fit_policy_version": FIT_POLICY_VERSION,
        "pairing_rule": pairing_rule(),
        "min_pairs_per_held_out_athlete": MIN_PAIRS_PER_HELD_OUT_ATHLETE,
        "real": _population_census([r for r in all_rows if r.get("user_id") not in seeded], pairs),
        "seeded": _population_census([r for r in all_rows if r.get("user_id") in seeded], pairs),
    }


def format_census(result: Mapping[str, Any]) -> str:
    rule = result["pairing_rule"]
    lines = [
        f"CENSUS (fit policy {result['fit_policy_version']}; next session within "
        f"{rule['gap_days'][0]}-{rule['gap_days'][1]} days; causal baseline over >= "
        f"{rule['min_prior_sessions']} earlier sessions)",
    ]
    for population in ("real", "seeded"):
        pop = result[population]
        lines.append(
            f"  {population}: {pop['rows']} rows, {pop['athletes']} athletes, "
            f"{pop['session_at_first']} .. {pop['session_at_last']}"
        )
        for version, v in pop["by_v1_model_version"].items():
            depth = v["athletes_with_at_least_n_eligible_sessions"]
            lines += [
                f"    {version}: rows {v['rows']}, eligible {v['eligible_rows']}, "
                f"labelled pairs {v['labelled_pairs']}, holdout-capable athletes "
                f"{v['athletes_holdout_capable']}",
                f"      tiers {v['rows_by_fit_tier']}",
                f"      athletes with >=2/3/5/10 eligible sessions: "
                f"{depth['2']}/{depth['3']}/{depth['5']}/{depth['10']}",
                f"      pair status {v['eligible_rows_by_pair_status']}",
            ]
    return "\n".join(lines)


def _fmt_stats(s: Mapping[str, float | int]) -> str:
    return (
        f"n={s['n']:<5} median={s['median']:.3f}  p10={s['p10']:.3f}  "
        f"p90={s['p90']:.3f}  min={s['min']:.3f}  max={s['max']:.3f}"
    )


def format_report(result: Mapping[str, Any]) -> str:
    lines = [HEADER, ""]
    if result["status"] == "no_rows":
        lines.append("no rows: the shadow log is empty.")
        return "\n".join(lines)

    lines += [
        f"total rows:                 {result['total_rows']}",
        f"fabricated-set rows:        {result['fabricated_set_rows']} "
        f"(excluded: {result['fabricated_set_rows_excluded']})",
        f"analysed rows:              {result['analysed_rows']}",
        f"distinct athletes:          {result['distinct_users']}",
        f"sessions from / to:         {result['session_at_first']} / {result['session_at_last']}",
        f"rows without code_version:  {result['rows_without_code_version']}",
        f"rows with undefined ratio:  {result['rows_with_undefined_ratio']}",
        "",
    ]
    if result["status"] == "no_rows_after_exclusion":
        lines.append("no rows left after excluding fabricated-set rows (--include-fabricated shows them).")
        return "\n".join(lines)
    if result["overall"] is None:
        lines.append("no row has a defined ratio (every v0 total was zero).")
        return "\n".join(lines)

    lines.append(f"overall  {_fmt_stats(result['overall'])}")
    for name, buckets in result["groups"].items():
        lines += ["", f"by {name}:"]
        width = max(len(label) for label in buckets)
        lines += [f"  {label:<{width}}  {_fmt_stats(s)}" for label, s in buckets.items()]
    return "\n".join(lines)


async def _load() -> tuple[list[dict[str, Any]], list[LoggedSession], set[int]]:
    from app.core.db import AsyncSessionLocal
    from app.models.user import User
    from app.models.workout_log import WorkoutLog

    cols = [getattr(DoseModelShadowLog, c) for c in REPORT_COLUMNS]
    async with AsyncSessionLocal() as db:
        rows = [dict(m) for m in (await db.execute(select(*cols))).mappings().all()]
        logged = await db.execute(
            select(
                WorkoutLog.id,
                WorkoutLog.user_id,
                WorkoutLog.session_timestamp,
                WorkoutLog.session_rpe,
            )
        )
        sessions = [
            LoggedSession(workout_log_id=i, user_id=u, at=t, session_rpe=float(rpe))
            for i, u, t, rpe in logged.all()
        ]
        users = await db.execute(select(User.id, User.email))
        seeded = {uid for uid, email in users.all() if is_seeded_email(email)}
    return rows, sessions, seeded


async def _run(*, include_fabricated: bool, as_json: bool) -> None:
    rows, sessions, seeded = await _load()
    counted = census(rows, sessions, seeded)
    ratios = summarize(rows, include_fabricated=include_fabricated)
    if as_json:
        print(json.dumps({"census": counted, "ratios": ratios}, indent=2, default=str))
    else:
        print(format_census(counted) + "\n\n" + format_report(ratios))


def main() -> None:
    ap = argparse.ArgumentParser(description="v1/v0 dose ratios captured by the 8A shadow log.")
    ap.add_argument(
        "--include-fabricated",
        action="store_true",
        help="Keep rows where v0 counted fabricated sets (excluded by default, backlog C2).",
    )
    ap.add_argument("--json", action="store_true", help="Emit the raw result as JSON.")
    args = ap.parse_args()
    asyncio.run(_run(include_fabricated=args.include_fabricated, as_json=args.json))


if __name__ == "__main__":
    main()
