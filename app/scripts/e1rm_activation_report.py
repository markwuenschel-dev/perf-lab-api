"""Before turning on chart e1RM estimates: a counterfactual restatement of in-window history (P4-2b).

Read-only. Run it first, read it, then decide::

    python -m app.scripts.e1rm_activation_report               # every athlete with a selected basis
    python -m app.scripts.e1rm_activation_report --user-id 7

For each athlete and lift it prints the e1RM that sizes a load today, the one it would be if the
Epley-derived sets that qualify today had been estimated with the chart, the difference in percent,
and the factor ``load / e1rm_pre`` (the dose ladder's relative load) would be multiplied by.

This is NOT a forecast of what happens at activation. History stays Epley, so a future set competes
with the existing rows by value and only raises the basis if it is the highest eligible one. Only
rows with a recorded Epley formula that could size a load today are restated. Nothing is written.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from app.core.db import AsyncSessionLocal
from app.services.e1rm_activation import ActivationLine, activation_report


def _format(lines: list[ActivationLine]) -> list[str]:
    tag = "[e1rm-activation]"
    if not lines:
        return [f"{tag} No athlete has an e1RM basis to compare."]
    out = [f"{tag} {'user':>6} {'lift':<22} {'today kg':>9} {'restated':>9} {'diff':>8} {'rel.load x':>11} {'rows':>5}"]
    steps: list[float] = []
    for line in lines:
        step = line.step_pct
        factor = line.relative_load_factor
        if step is not None:
            steps.append(step)
        fmt = lambda v, spec: format(v, spec) if v is not None else "-"  # noqa: E731
        out.append(
            f"{tag} {line.user_id:>6} {line.code:<22} {fmt(line.basis_now_kg, '9.1f'):>9} "
            f"{fmt(line.basis_chart_kg, '9.1f'):>9} {fmt(step, '+7.1f') + '%':>8} "
            f"{fmt(factor, '10.3f'):>11} {line.restated_rows:>5}"
        )
    moved = [s for s in steps if abs(s) > 0.05]
    if moved:
        out.append(
            f"{tag} {len(moved)} of {len(lines)} basis(es) differ in the restatement; "
            f"range {min(moved):+.1f}% to {max(moved):+.1f}%. Counterfactual, not a forecast. "
            f"Read-only: nothing was changed."
        )
    else:
        out.append(f"{tag} No basis differs in the restatement. Read-only: nothing was changed.")
    return out


async def _run(user_id: int | None) -> list[ActivationLine]:
    async with AsyncSessionLocal() as db:
        return await activation_report(db, as_of=datetime.now(UTC).replace(tzinfo=None), user_id=user_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--user-id", type=int, default=None, help="only this athlete")
    args = parser.parse_args(argv)
    print("\n".join(_format(asyncio.run(_run(args.user_id)))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
