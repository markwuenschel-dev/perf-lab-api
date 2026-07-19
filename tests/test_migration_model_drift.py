"""Model ↔ migration structural-drift gate (INT-03).

After the Alembic chain is upgraded to head (the ``async_db`` fixture does this
on a freshly-dropped schema), autogenerate must detect no **structural** drift
between the live schema and ``Base.metadata`` — no table or column present in one
but missing from the other, and no column whose type silently changed. That is
the class of drift that breaks production queries.

Scope note: autogenerate also reports *cosmetic* metadata drift — column comments,
``NOT NULL`` flags, and index/constraint naming — that accumulated in this repo (109
such diffs at introduction, 0 structural). Those do not change query correctness, and
reconciling them is a separate, migration-heavy cleanup (adding ``NOT NULL`` needs a
data audit; renaming indexes is churn). Rather than fail on that backlog, the cosmetic
categories are held to a **per-category shrink-only ratchet** (AUD-C7,
``test_cosmetic_drift_does_not_grow``): each category must exactly match a reviewed
baseline, so a regression fails, a reduction fails until the baseline is lowered in the
same PR (the debt can never quietly regain slack), and a brand-new drift category fails
until it is reviewed into the baseline. The baseline is *containment, not closure* — the
``modify_nullable`` count is the C12 nullability surface, tracked as debt with a separate
owner, not blessed as correct.

This is a ``requires_db`` test (uses ``async_db``); it skips only when Postgres is
unreachable, and fails loudly on real structural drift.
"""
import collections

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.engine import Connection

# Import models so every table is registered on Base.metadata before comparison.
import app.models  # noqa: F401
from app.core.db import Base

pytestmark = pytest.mark.asyncio

# Autogenerate ops that change the schema *shape* — a query written against the
# models would break if the live schema disagreed on any of these.
STRUCTURAL_OPS = frozenset(
    {"add_table", "remove_table", "add_column", "remove_column", "modify_type"}
)


def _op_name(diff: object) -> str:
    """The operation string of a compare_metadata entry.

    Entries are either a tuple ``('add_table', ...)`` or, for grouped
    column-level modifications, a list whose first element is such a tuple.
    """
    if isinstance(diff, list):
        return str(diff[0][0])
    return str(diff[0])  # type: ignore[index]


def _diffs(sync_conn: Connection) -> list:
    context = MigrationContext.configure(
        connection=sync_conn,
        opts={
            # Compare column *types* too (Integer→BigInteger etc.). Server defaults
            # are excluded: Postgres round-trips them as dialect-specific text that
            # produces noisy false positives.
            "compare_type": True,
            "compare_server_default": False,
        },
    )
    return compare_metadata(context, Base.metadata)


# Reviewed per-category baseline of KNOWN cosmetic (non-structural) drift, from a live
# autogenerate run. SHRINK-ONLY: `test_cosmetic_drift_does_not_grow` asserts each actual
# count *equals* its baseline, so this dict is the single reviewed source of truth —
# raising an entry accepts new drift (reviewed here in the PR diff), lowering one locks in
# a fix, and neither may drift without a code change a reviewer sees. `modify_nullable` is
# the C12 nullability surface: tracked debt with a separate owner, contained here, not
# declared correct.
COSMETIC_DRIFT_BASELINE = {
    "modify_comment": 43,
    "modify_nullable": 29,
    "add_index": 13,
    "remove_index": 7,
    "remove_constraint": 2,
}


async def test_no_structural_drift(async_db):
    """Models and the Alembic head agree on every table, column, and column type."""
    conn = await async_db.connection()
    diffs = await conn.run_sync(_diffs)

    structural = [d for d in diffs if _op_name(d) in STRUCTURAL_OPS]
    assert structural == [], (
        "Structural model/migration drift — a table, column, or column type "
        "differs between the models and the Alembic head. Generate a migration "
        "(or fix the model):\n" + "\n".join(f"  - {d}" for d in structural)
    )


async def test_cosmetic_drift_does_not_grow(async_db):
    """Per-category shrink-only ratchet on cosmetic (non-structural) drift (AUD-C7).

    Each cosmetic category must match its reviewed ``COSMETIC_DRIFT_BASELINE`` exactly:
    a regression (count above baseline) fails, a reduction (count below baseline) fails
    until the baseline is lowered in the same PR — so no category can quietly regain slack
    and offset another's regression — and a brand-new drift category fails until it is
    reviewed into the baseline. Containment of known debt, not a claim it is correct.
    """
    conn = await async_db.connection()
    diffs = await conn.run_sync(_diffs)

    by_op: collections.Counter[str] = collections.Counter(_op_name(d) for d in diffs)
    cosmetic = {op: n for op, n in by_op.items() if op not in STRUCTURAL_OPS}

    unknown = sorted(set(cosmetic) - set(COSMETIC_DRIFT_BASELINE))
    assert not unknown, (
        f"new cosmetic drift categor(ies) not in the reviewed baseline: "
        f"{ {op: cosmetic[op] for op in unknown} }. Reconcile the drift, or add the "
        "category to COSMETIC_DRIFT_BASELINE with review explaining the accepted drift."
    )

    regressions = {
        op: (cosmetic.get(op, 0), base)
        for op, base in COSMETIC_DRIFT_BASELINE.items()
        if cosmetic.get(op, 0) > base
    }
    assert not regressions, (
        "cosmetic drift GREW past its baseline {actual > baseline}: "
        f"{regressions}. Generate a migration to reconcile it — a category may not expand "
        "(and cannot be offset by a shrink elsewhere, since each is gated independently)."
    )

    reductions = {
        op: (cosmetic.get(op, 0), base)
        for op, base in COSMETIC_DRIFT_BASELINE.items()
        if cosmetic.get(op, 0) < base
    }
    assert not reductions, (
        "cosmetic drift SHRANK below its baseline {actual < baseline}: "
        f"{reductions}. Good — now lower the corresponding COSMETIC_DRIFT_BASELINE entr"
        "y(ies) to lock the reduction in, so the slack can't return as a silent regression."
    )
