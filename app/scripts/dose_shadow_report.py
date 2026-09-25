"""Report what the dose-model shadow log (phase 8A) has captured: v1/v0 ratios, grouped.

Read-only, and it authorizes nothing. A ratio says how differently v1 scores a session than
v0 does, not which of them is right. Deciding that is phase 8B's fit and 8C's validation, and
this report only tells you whether there is enough real data to start.

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

from app.models.dose_model_shadow import DoseModelShadowLog

#: The columns the report reads. Kept narrow: the JSON detail columns are not needed here.
REPORT_COLUMNS: tuple[str, ...] = (
    "user_id",
    "session_at",
    "code_version",
    "modality",
    "planned_domain",
    "planned_category",
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


async def _load_rows() -> list[dict[str, Any]]:
    from app.core.db import AsyncSessionLocal

    cols = [getattr(DoseModelShadowLog, c) for c in REPORT_COLUMNS]
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(*cols))
        return [dict(m) for m in result.mappings().all()]


async def _run(*, include_fabricated: bool, as_json: bool) -> None:
    result = summarize(await _load_rows(), include_fabricated=include_fabricated)
    print(json.dumps(result, indent=2, default=str) if as_json else format_report(result))


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
