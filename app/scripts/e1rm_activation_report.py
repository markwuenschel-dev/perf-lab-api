"""Before turning on chart e1RM estimates: what it would do to each athlete's loads (P4-2b).

Read-only. Run it first, read it, then decide::

    python -m app.scripts.e1rm_activation_report               # every athlete with a selected basis
    python -m app.scripts.e1rm_activation_report --user-id 7

For each athlete and lift it prints the e1RM that sizes a load today, the one that would with
``E1RM_CHART_ESTIMATES=true``, the step in percent, and the factor the dose ladder's relative load
(``load / e1rm_pre``) is multiplied by, because the same number is the dose denominator. The step
arrives for an athlete on their next qualifying set, in one move (the selector takes the highest
eligible value). Rows already estimated by the chart are not restated. Nothing is written.
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
    out = [f"{tag} {'user':>6} {'lift':<22} {'today kg':>9} {'chart kg':>9} {'step':>8} {'rel.load x':>11} {'rows':>5}"]
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
            f"{tag} {len(moved)} of {len(lines)} basis(es) move; "
            f"range {min(moved):+.1f}% to {max(moved):+.1f}%. Read-only: nothing was changed."
        )
    else:
        out.append(f"{tag} No basis would move. Read-only: nothing was changed.")
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
